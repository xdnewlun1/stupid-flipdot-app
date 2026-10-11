"""The fast versions of the per-dot algorithms against straightforward per-dot references.

fastpath's C functions against its own Python twins; the slice / big-integer / Pillow rewrites in transitions,
slidefx, charfx, flipdot and the generative apps against the per-dot loops they replaced (kept here as the
reference), dot for dot.

    python3 -m unittest tests.test_fast
    FLIPDOT_NO_NATIVE=1 python3 -m unittest tests.test_fast   # the pure-Python paths
"""

import math
import random
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import apps  # noqa: E402,F401  (first: charfx imports apps.base, and apps imports charfx)
import fastpath  # noqa: E402
import transitions  # noqa: E402
from canvas import Canvas  # noqa: E402
from charfx import crop  # noqa: E402
from apps.slidefx import Effect  # noqa: E402

W, H = 84, 42


def pic(rnd, w=W, h=H, density=0.5):
    c = Canvas(w, h)
    c.px = bytearray(1 if rnd.random() < density else 0 for _ in range(w * h))
    return c


def blobs(rnd, w=W, h=H):
    """A picture with shapes in it (distance fields and outlines are dull on noise)."""
    c = Canvas(w, h)
    for _ in range(rnd.randint(1, 6)):
        c.rect(rnd.randrange(-5, w), rnd.randrange(-5, h), rnd.randint(2, 30), rnd.randint(2, 20), fill=True)
    for _ in range(rnd.randint(0, 3)):
        c.circle(rnd.randrange(w), rnd.randrange(h), rnd.randint(1, 12), fill=rnd.random() < 0.5)
    return c


class FastpathTest(unittest.TestCase):
    def test_distance_field_matches_python(self):
        rnd = random.Random(1)
        for _ in range(20):
            w, h = rnd.choice(((84, 42), (28, 7), (5, 3), (1, 1), (12, 40)))
            c = blobs(rnd, w, h) if rnd.random() < 0.7 else pic(rnd, w, h)
            self.assertEqual(list(fastpath.distance_field(c.px, w, h)), list(fastpath._distance_field_py(c.px, w, h)))

    def test_distance_field_values(self):
        c = Canvas(9, 5)
        c.rect(3, 1, 3, 3, fill=True)  # a 3x3 white block
        d = fastpath.distance_field(c.px, 9, 5)
        self.assertEqual(d[2 * 9 + 4], 6)   # the middle dot: two straight steps to black
        self.assertEqual(d[1 * 9 + 3], 3)   # a corner of the block: one step
        self.assertEqual(d[0], -10)         # the board's corner: a diagonal step and two straight ones (4 + 3 + 3) to white
        self.assertEqual(d[2 * 9 + 0], -9)  # three straight steps

    def test_morph_blend_matches_python(self):
        rnd = random.Random(2)
        for _ in range(10):
            a, b = (fastpath.distance_field(blobs(rnd).px, W, H) for _ in range(2))
            for p in (0.0, 0.1, 0.5, 0.73, 1.0):
                self.assertEqual(fastpath.morph_blend(a, b, p), fastpath._morph_blend_py(a, b, p))

    def test_life_step_matches_python(self):
        rnd = random.Random(3)
        for _ in range(10):
            w, h = rnd.choice(((84, 42), (7, 3), (1, 1), (2, 2), (30, 9)))
            cells = pic(rnd, w, h, 0.4).px
            self.assertEqual(bytes(fastpath.life_step(cells, w, h)), bytes(fastpath._life_step_py(cells, w, h)))

    def test_life_blinker(self):
        c = Canvas(5, 5)
        for x in (1, 2, 3):
            c.set(x, 2)
        nxt = fastpath.life_step(c.px, 5, 5)
        self.assertEqual([nxt[y * 5 + 2] for y in range(5)], [0, 1, 1, 1, 0])
        self.assertEqual(sum(nxt), 3)


# ---- the per-dot loops the transitions replaced --------------------------------------------------------

def ref_slide(direction, push, old, new, p):
    dx, dy = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}[direction]
    w, h = old.w, old.h
    out = Canvas(w, h)
    ox, oy = round(-dx * w * (1 - p)), round(-dy * h * (1 - p))
    px_, py_ = round(dx * w * p), round(dy * h * p)
    for y in range(h):
        for x in range(w):
            nx, ny = x - ox, y - oy
            if 0 <= nx < w and 0 <= ny < h:
                out.px[y * w + x] = new.px[ny * w + nx]
            elif push:
                out.px[y * w + x] = old.get(x - px_, y - py_)
            else:
                out.px[y * w + x] = old.px[y * w + x]
    return out


def ref_flip(old, new, p):
    w, h = old.w, old.h
    out = Canvas(w, h)
    src, s = (old, 1 - 2 * p) if p < 0.5 else (new, 2 * p - 1)
    if s <= 0.02:
        out.rect(w // 2, 0, 1, h, fill=True)
        return out
    cx = (w - 1) / 2
    for x in range(w):
        fx = round((x - cx) / s + cx)
        if 0 <= fx < w:
            for y in range(h):
                out.px[y * w + x] = src.px[y * w + fx]
    return out


def ref_melt(speeds, old, new, p):
    w, h = old.w, old.h
    out = new.copy()
    for x in range(w):
        drop = round(h * min(1.0, p * speeds[x]) ** 2)
        for y in range(drop, h):
            out.px[y * w + x] = old.px[(y - drop) * w + x]
    return out


def ref_pixelate(px, w, h, block):
    out = bytearray(w * h)
    for by in range(0, h, block):
        for bx in range(0, w, block):
            cells = [px[y * w + x] for y in range(by, min(by + block, h)) for x in range(bx, min(bx + block, w))]
            v = 1 if sum(cells) * 2 >= len(cells) else 0
            for y in range(by, min(by + block, h)):
                for x in range(bx, min(bx + block, w)):
                    out[y * w + x] = v
    return bytes(out)


def ref_reveal(t, new, p):
    out = t.old.copy()
    order, times = t.order, t.times
    i, n = 0, len(order)
    while i < n and (times[order[i]] < p or p >= 1):
        out.px[order[i]] = new.px[order[i]]
        i += 1
    if t.edge and 0 < p < 1:
        while i < n and times[order[i]] <= p + 0.04:
            out.px[order[i]] = 1
            i += 1
    return out


class TransitionsTest(unittest.TestCase):
    def setUp(self):
        self.rnd = random.Random(4)

    def test_slides_and_pushes(self):
        old, new = blobs(self.rnd), blobs(self.rnd)
        for direction in ("left", "right", "up", "down"):
            for push in (False, True):
                for p in (0.0, 0.2, 0.5, 0.77, 1.0):
                    got = transitions._slide(direction, push)(None, old, new, p)
                    self.assertEqual(got.px, ref_slide(direction, push, old, new, p).px, (direction, push, p))

    def test_flip(self):
        old, new = blobs(self.rnd), blobs(self.rnd)
        for p in (0.0, 0.1, 0.49, 0.5, 0.51, 0.9, 1.0):
            self.assertEqual(transitions._flip(None, old, new, p).px, ref_flip(old, new, p).px, p)

    def test_melt(self):
        old, new = blobs(self.rnd), blobs(self.rnd)
        t = SimpleNamespace(state={}, rnd=self.rnd)
        for p in (0.0, 0.1, 0.3, 0.6, 1.0):
            got = transitions._melt(t, old, new, p)
            self.assertEqual(got.px, ref_melt(t.state["speeds"], old, new, p).px, p)

    def test_ghost_is_xor(self):
        old, new = pic(self.rnd), pic(self.rnd)
        got = transitions._ghost(None, old, new, 0.5)
        self.assertEqual(got.px, bytearray(a ^ b for a, b in zip(old.px, new.px)))

    def test_pixelate_blocks(self):
        for _ in range(6):
            c = blobs(self.rnd) if self.rnd.random() < 0.5 else pic(self.rnd)
            for block in (2, 3, 4, 6, 10):
                got, ref = transitions.pixelate(bytes(c.px), W, H, block), ref_pixelate(c.px, W, H, block)
                self.assertEqual(got, ref, block)  # exact: see _HALF in transitions.py
                # whole blocks are uniform
                for by in range(0, H, block):
                    for bx in range(0, W, block):
                        vals = {got[y * W + x] for y in range(by, min(by + block, H)) for x in range(bx, min(bx + block, W))}
                        self.assertEqual(len(vals), 1)

    def test_vortex_warp(self):
        # no twist, no zoom: the picture as it was; the map is cached and reused
        c = pic(self.rnd)
        self.assertEqual(transitions.vortex(c.px, W, H, 0.0), bytes(c.px))
        m1, m2 = transitions.vortex_map(W, H, 1.234), transitions.vortex_map(W, H, 1.2341)
        self.assertIs(m1, m2)
        # a twist turns the middle of the picture, and leaves the corners where they are
        c = Canvas(W, H)
        c.rect(0, 20, 84, 3, fill=True)  # a bar across the middle
        got = transitions.vortex(c.px, W, H, math.pi / 2)
        self.assertEqual(got[21 * W + 41], 1)  # still through the centre...
        self.assertTrue(any(got[y * W + x] for y in range(16, 27) for x in range(4)))  # ...the ends barely move...
        self.assertTrue(any(got[y * W + x] for y in range(16, 27) for x in range(80, 84)))
        self.assertEqual(got[21 * W + 30], 0)  # ...but between them the bar has swung away
        self.assertTrue(any(got[14 * W + x] for x in range(40, 46)))  # ...to run up and down through the middle
        c.rect(0, 0, 3, 3, fill=True)
        self.assertEqual(transitions.vortex(c.px, W, H, math.pi)[0], 1)  # no twist at all in the corner
        # drawing in: the picture shrinks towards the middle, the outside goes black
        c = Canvas(W, H, fill=1)
        got = transitions.vortex(c.px, W, H, 0.0, zoom=2.0)
        self.assertEqual(got[21 * W + 41], 1)
        self.assertEqual(got[0], 0)
        self.assertEqual(got[21 * W + 2], 0)
        self.assertEqual(got[21 * W + 25], 1)

    def test_vortex_transition_starts_old_and_ends_new(self):
        old, new = blobs(self.rnd), blobs(self.rnd)
        t = SimpleNamespace(state={}, rnd=self.rnd)
        self.assertEqual(transitions._vortex(t, old, new, 0.0).px, old.px)
        self.assertEqual(transitions._vortex(t, old, new, 1.0).px, new.px)
        mid = transitions._vortex(t, old, new, 0.5).px
        self.assertNotEqual(mid, old.px)
        self.assertNotEqual(mid, new.px)
        tr = transitions.Transition("vortex", old, W, H, 1.0)
        tr.start = 0.0
        self.assertEqual(tr.mix(new, 1.0).px, new.px)
        self.assertIn("vortex", transitions.NAMES)

    def test_new_transitions_start_old_and_end_new(self):
        old, new = Canvas(W, H), Canvas(W, H)
        old.rect(4, 4, 30, 20, fill=True)
        new.rect(50, 16, 30, 20, fill=True)
        for name in ("page-curl", "shatter", "ripple-out", "wave-morph", "typewriter"):
            tr = transitions.Transition(name, old, W, H, 1.0)
            tr.start = 0.0
            self.assertEqual(tr.mix(new, 0.0).px, old.px, name)
            self.assertEqual(tr.mix(new, 1.0).px, new.px, name)
            mids = [bytes(tr.mix(new, p).px) for p in (0.3, 0.5, 0.7)]
            for m in mids:
                self.assertNotEqual(m, bytes(old.px), name)
                self.assertNotEqual(m, bytes(new.px), name)
            self.assertGreater(len(set(mids)), 1, name)
            self.assertIn(name, transitions.NAMES)

    def test_typewriter_pops_whole_cells_in_reading_order(self):
        old, new = Canvas(W, H), Canvas(W, H, fill=1)
        tr = transitions.Transition("typewriter", old, W, H, 1.0)
        tr.start = 0.0
        got = tr.mix(new, 0.5)  # about half the cells typed
        cols, rows = -(-W // 6), -(-H // 8)
        typed = [all(got.px[y * W + x] for y in range(cy * 8, min(H, cy * 8 + 8)) for x in range(cx * 6, min(W, cx * 6 + 6)))
                 for cy in range(rows) for cx in range(cols)]
        first_blank = typed.index(False)
        self.assertGreater(first_blank, 5)
        self.assertTrue(all(typed[:first_blank]))      # everything before it is typed...
        self.assertFalse(any(typed[first_blank + 1:]))  # ...and nothing after (the cursor cell may be lit)

    def test_moods_resolve_to_their_members(self):
        for mood, members in transitions.MOODS.items():
            self.assertIn(mood, transitions.NAMES)
            for _ in range(20):
                tr = transitions.Transition(mood, Canvas(W, H), W, H, 1.0)
                self.assertIn(tr.kind, members)
                self.assertTrue(tr.kind in transitions.REVEALS or tr.kind in transitions.MOVES)
        for _ in range(30):
            self.assertNotIn(transitions.Transition("random", Canvas(W, H), W, H).kind, transitions.MOODS)

    def test_reveal_mix_matches_per_dot_loop(self):
        for kind in ("dissolve", "wipe-right", "iris-open", "clock", "spiral", "tiles", "interlace"):
            old = blobs(self.rnd)
            t = transitions.Transition(kind, old, W, H, 1.0)
            new = blobs(self.rnd)
            ps = sorted(self.rnd.uniform(0, 1) for _ in range(12)) + [1.0, 1.0]
            for p in ps:
                live_old = blobs(self.rnd) if self.rnd.random() < 0.3 else old  # the old picture may keep animating
                t.old = live_old
                # drive mix() at the time that gives exactly this p (smoothstep is monotonic: invert by search)
                lo, hi = 0.0, 1.0
                for _ in range(40):
                    mid = (lo + hi) / 2
                    lo, hi = (mid, hi) if transitions.smoothstep(mid) < p else (lo, mid)
                now = t.start + hi * t.duration
                got = t.mix(new, now, live_old)
                self.assertEqual(got.px, ref_reveal(t, new, transitions.smoothstep(min(1.0, hi))).px, (kind, p))

    def test_reveal_mix_restarts_when_the_clock_goes_back(self):
        t = transitions.Transition("wipe-right", blobs(self.rnd), W, H, 1.0)
        new = blobs(self.rnd)
        t.mix(new, t.start + 0.8)
        got = t.mix(new, t.start + 0.2)
        self.assertEqual(got.px, ref_reveal(t, new, transitions.smoothstep(0.2)).px)

    def test_morph_runs(self):
        old, new = blobs(self.rnd), blobs(self.rnd)
        t = transitions.Transition("morph", old, W, H, 1.0)
        self.assertEqual(t.mix(new, t.start, old).px, old.px)  # p = 0: the old picture
        self.assertEqual(t.mix(new, t.start + 2, old).px, new.px)  # done: the new one
        mid = t.mix(new, t.start + 0.5, old)
        self.assertEqual(set(mid.px) - {0, 1}, set())

    def test_life_transition_settles(self):
        old, new = pic(self.rnd), blobs(self.rnd)
        t = transitions.Transition("life", old, W, H, 1.0)
        t.mix(new, t.start + 0.5, old)
        self.assertEqual(t.mix(new, t.start + 0.999, old).px.count(1) > 0, True)
        self.assertEqual(t.mix(new, t.start + 1.0, old).px, new.px)


# ---- slideshow effects, charfx, the board's flip counter -----------------------------------------------

def ref_outline(src, w, h):
    out = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            v = src[y * w + x]
            if v and ((x and not src[y * w + x - 1]) or (x < w - 1 and not src[y * w + x + 1])
                      or (y and not src[(y - 1) * w + x]) or (y < h - 1 and not src[(y + 1) * w + x])):
                out[y * w + x] = 1
    return out


def ref_crop(c, x, y, w, h):
    out = Canvas(w, h)
    for yy in range(h):
        sy = y + yy
        if 0 <= sy < c.h:
            for xx in range(w):
                sx = x + xx
                if 0 <= sx < c.w:
                    out.px[yy * w + xx] = c.px[sy * c.w + sx]
    return out


class EffectsTest(unittest.TestCase):
    def setUp(self):
        self.rnd = random.Random(5)

    def test_outline(self):
        for _ in range(8):
            c = blobs(self.rnd) if self.rnd.random() < 0.7 else pic(self.rnd)
            ref = ref_outline(bytes(c.px), W, H)
            e = Effect("outline", 0, None, 0.5, self.rnd)
            self.assertEqual(e.apply(c, 1.0).px, ref)

    def test_melt_effect(self):
        c = blobs(self.rnd)
        e = Effect("melt", 0, None, 0.5, self.rnd)
        got = e.apply(c, 1.5)
        speeds, rate = e.state["speeds"], 4 + 14 * e.strength
        ref = bytearray(c.px)
        src = bytes(c.px)
        for x in range(W):
            drop = min(H, int((1.5 * rate * speeds[x]) ** 1.3))
            if drop:
                for y in range(H - 1, -1, -1):
                    sy = y - drop
                    ref[y * W + x] = src[sy * W + x] if sy >= 0 else 0
        self.assertEqual(got.px, ref)

    def test_vortex_effect_sways_with_the_strength(self):
        c = blobs(self.rnd)
        e = Effect("vortex", 0, None, 1.0, self.rnd)
        self.assertEqual(e.apply(c, 0.0).px, c.px)  # the sway starts at rest...
        peak = e.apply(c, 0.5).px                    # ...and is furthest half a cycle in (2 s cycle at 1x)
        self.assertNotEqual(peak, c.px)
        self.assertEqual(e.apply(c, 1.0).px, c.px)   # back through rest
        e.strength = 0.3
        self.assertNotEqual(e.apply(c, 0.5).px, peak)  # a weaker strength twists less
        self.assertEqual(peak, bytes(transitions.vortex(c.px, W, H, Effect.VORTEX_TURN)) or peak)

    def test_new_effects_change_the_picture_and_leave_it_at_zero(self):
        c = blobs(self.rnd)
        for name in ("pulse", "ripple", "shake", "echo", "strobe"):
            e = Effect(name, 0, None, 1.0, self.rnd)
            e.reacting = True
            e.strength = 1.0
            changed = False
            for i in range(8):
                src = blobs(self.rnd) if name == "echo" else c
                changed |= e.apply(src, 0.05 * i).px != src.px
            self.assertTrue(changed, name)
            e.idle()
            e.strength = 0.0
            e.prev_strength = 0.0
            still = Effect(name, 0, None, 1.0, self.rnd)
            still.reacting, still.strength = True, 0.0
            for i in range(3):
                self.assertEqual(still.apply(c, 0.05 * i).px, c.px, name)  # the strength never passed half: no hit

    def test_echo_leaves_a_trail_and_forgets_it_when_idle(self):
        e = Effect("echo", 0, None, 1.0, self.rnd)
        e.reacting, e.strength = True, 1.0
        a, b = Canvas(W, H), Canvas(W, H)
        a.rect(10, 10, 6, 6, fill=True)
        b.rect(40, 10, 6, 6, fill=True)
        e.apply(a, 0.0)
        got = e.apply(b, 0.1)
        self.assertEqual(got.get(42, 12), 1)
        self.assertGreater(sum(got.px[12 * W + 10:12 * W + 16]), 0)  # some of the old square is still there
        e.idle()
        self.assertEqual(e.apply(b, 0.2).px, b.px)  # the trail was forgotten

    def test_shake_offsets_the_whole_picture_on_a_hit(self):
        c = Canvas(W, H)
        c.rect(30, 15, 20, 10, fill=True)
        e = Effect("shake", 0, None, 1.0, random.Random(3))
        e.reacting = True
        e.strength = 0.0
        e.apply(c, 0.0)
        e.strength = 1.0  # a hit
        got = e.apply(c, 0.05)
        self.assertNotEqual(got.px, c.px)
        self.assertEqual(sum(got.px), sum(c.px))  # moved, not changed (the square stays on the board)
        later = e.apply(c, 1.0)
        self.assertEqual(later.px, c.px)  # settled

    def test_glitch_only_shifts_and_inverts_rows(self):
        c = blobs(self.rnd)
        got = Effect("glitch", 0, None, 1.0, self.rnd).apply(c, 1.0)
        for y in range(H):
            row, orig = got.px[y * W:(y + 1) * W], c.px[y * W:(y + 1) * W]
            inv = bytearray(1 - v for v in orig)
            self.assertTrue(any(row == (r[k:] + r[:k]) for r in (orig, inv) for k in range(W)), y)

    def test_crop(self):
        for _ in range(60):
            c = pic(self.rnd, self.rnd.randint(1, 30), self.rnd.randint(1, 20))
            x, y, w, h = self.rnd.randint(-10, 35), self.rnd.randint(-8, 25), self.rnd.randint(1, 14), self.rnd.randint(1, 12)
            self.assertEqual(crop(c, x, y, w, h).px, ref_crop(c, x, y, w, h).px, (c.w, c.h, x, y, w, h))

    def test_count_flips(self):
        from flipdot import Board
        cfg = {"panels": [{"column": 1, "row": 1, "ip": "127.0.0.1", "port": 1, "address": 1},
                          {"column": 2, "row": 1, "ip": "127.0.0.1", "port": 1, "address": 2, "rotate": 180}]}
        b = Board(cfg, dry_run=True)
        ref = [0] * (b.width * b.height)
        for i, p in enumerate(b.panels):
            frames = [bytes([0x80, 0x83, p.address]) + bytes(self.rnd.randrange(128) for _ in range(28)) + b"\x8f" for _ in range(6)]
            for old, new in zip(frames, frames[1:]):
                b._sent[i] = old
                b._count_flips(i, p, new)
                for cx in range(28):
                    d = old[3 + cx] ^ new[3 + cx]
                    for cy in range(7):
                        if d >> cy & 1:
                            sx, sy = (27 - cx, 6 - cy) if p.rotate == 180 else (cx, cy)
                            ref[(p.row * 7 + sy) * b.width + p.col * 28 + sx] += 1
        self.assertEqual(list(b.flips), ref)
        self.assertTrue(b.flips_changed)


class GenerativeTest(unittest.TestCase):
    """The generative apps' rewrites against the formulas they came from, dot for dot."""

    def setUp(self):
        self.ctx = SimpleNamespace(width=W, height=H, store=SimpleNamespace(settings={}), wake=None)

    def test_plasma(self):
        from apps.generative import BAYER4, Plasma
        for style in ("bands", "dither"):
            app = Plasma(self.ctx, style=style, speed=1.3, scale=0.8)
            now = app.started + 7.25
            c = Canvas(W, H)
            app.render(c, now)
            t, k = (now - app.started) * 1.3, 1 / 0.8
            cx, cy = W / 2 + 20 * math.sin(t * 0.3), H / 2 + 10 * math.cos(t * 0.4)
            diff = 0
            for y in range(H):
                for x in range(W):
                    v = (math.sin(x * k / 7 + t) + math.sin(y * k / 5 + t * 1.3)
                         + math.sin((x + y) * k / 9 + t * 0.7) + math.sin(math.hypot(x - cx, y - cy) * k / 5 - t))
                    on = int((v + 4) * 2) % 2 if style == "bands" else (v + 4) / 8 * 16 > BAYER4[y % 4][x % 4] + 0.5
                    diff += c.px[y * W + x] != (1 if on else 0)
            self.assertLessEqual(diff, 3, style)  # the sums are added in another order: a float tie or two

    def test_metaballs(self):
        from apps.effects import Metaballs
        for outline in (False, True):
            app = Metaballs(self.ctx, balls=5, outline=outline)
            c = Canvas(W, H)
            app.render(c, 0)
            balls = [(bx, by, r * r) for bx, by, _, _, r in app.balls]
            for y in range(H):
                for x in range(W):
                    f = sum(r2 / ((x - bx) ** 2 + ((y - by) * 1.6) ** 2 + 0.5) for bx, by, r2 in balls)
                    want = (1 if 1.0 < f < 1.35 else 0) if outline else (1 if f > 1.0 else 0)
                    self.assertEqual(c.px[y * W + x], want, (outline, x, y))

    def test_fire_rises(self):
        from apps.effects import Fire
        app = Fire(self.ctx)
        c = Canvas(W, H)
        for _ in range(30):
            app.render(c, time.time())
        bottom, top = sum(c.px[(H - 1) * W:]), sum(c.px[:W])
        self.assertGreater(bottom, W * 0.6)
        self.assertLess(top, bottom)
        self.assertTrue(all(0 <= v <= 36 for v in app.heat))


if __name__ == "__main__":
    unittest.main()
