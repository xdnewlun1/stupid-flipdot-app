"""The C port of the Doom outline look (native/doom/geodots.h) against the Python original (apps/doom.py),
dot for dot, on random and structured geometry planes. Needs gcc; skipped without it.

    python3 -m unittest tests.test_doom_geo
"""

import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.doom import G_CEIL, G_FLOOR, G_NONE, G_SKY, G_THING, G_WALL, G_WEAPON, draw_geometry  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include "geodots.h"
/* stdin: w h wireframe (ints), then w*h bytes of geometry; stdout: w*h bytes of dots */
int main(void) {
    int w, h, wire;
    if (scanf("%d %d %d", &w, &h, &wire) != 3) return 2;
    getchar();
    unsigned char *geo = malloc(w * h), *out = malloc(w * h);
    if (fread(geo, 1, w * h, stdin) != (size_t)(w * h)) return 3;
    geo_dots(geo, w, h, wire, out);
    fwrite(out, 1, w * h, stdout);
    return 0;
}
'''


def _scene(rnd, w, h):
    """A plane that looks like Doom: sky/ceiling above, floor below, walls at various distances, a few things
    and the weapon at the bottom middle, plus some random noise."""
    geo = bytearray(w * h)
    horizon = h // 2 + rnd.randint(-3, 3)
    for x in range(w):
        wall_top = horizon - rnd.randint(2, 12)
        wall_bot = horizon + rnd.randint(2, 12)
        dist = rnd.randint(0, 31)
        for y in range(h):
            if y < wall_top:
                t, d = (G_SKY, 31) if rnd.random() < 0.3 else (G_CEIL, max(0, dist - (wall_top - y)))
            elif y <= wall_bot:
                t, d = G_WALL, dist
            else:
                t, d = G_FLOOR, max(0, dist - (y - wall_bot))
            geo[y * w + x] = t << 5 | d
    for _ in range(rnd.randint(0, 4)):  # monsters/items
        bx, by, bw, bh, bd = rnd.randrange(w), rnd.randrange(h), rnd.randint(1, 8), rnd.randint(1, 10), rnd.randint(0, 31)
        for y in range(by, min(h, by + bh)):
            for x in range(bx, min(w, bx + bw)):
                geo[y * w + x] = G_THING << 5 | bd
    for y in range(max(0, h - rnd.randint(2, 8)), h):  # the weapon
        for x in range(w // 2 - 6, w // 2 + 6):
            if 0 <= x < w:
                geo[y * w + x] = G_WEAPON << 5 | 0
    for _ in range(w * h // 20):  # noise, including G_NONE
        i = rnd.randrange(w * h)
        geo[i] = rnd.choice((G_NONE, G_WALL, G_FLOOR, G_CEIL, G_SKY, G_THING, G_WEAPON)) << 5 | rnd.randint(0, 31)
    return bytes(geo)


@unittest.skipUnless(shutil.which("gcc"), "needs gcc")
class GeoDotsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        src = Path(cls.tmp) / "harness.c"
        src.write_text(HARNESS)
        cls.exe = Path(cls.tmp) / "harness"
        subprocess.run(["gcc", "-std=c99", "-O2", "-Wall", "-I", str(ROOT / "native" / "doom"), str(src), "-o", str(cls.exe)],
                       check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def c_dots(self, geo, w, h, wireframe):
        r = subprocess.run([str(self.exe)], input=f"{w} {h} {int(wireframe)}\n".encode() + geo, capture_output=True, check=True)
        return bytearray(r.stdout)

    def check(self, geo, w, h):
        for look in ("outline", "wireframe"):
            with self.subTest(look=look, w=w, h=h):
                self.assertEqual(self.c_dots(geo, w, h, look == "wireframe"), draw_geometry(geo, w, h, look))

    def test_scenes(self):
        rnd = random.Random(7)
        for _ in range(60):
            w, h = rnd.choice(((84, 36), (84, 42), (28, 7), (56, 21), (5, 3), (1, 1), (2, 9)))
            self.check(_scene(rnd, w, h), w, h)

    def test_random_planes(self):
        rnd = random.Random(11)
        for _ in range(60):
            w, h = rnd.randint(1, 90), rnd.randint(1, 45)
            self.check(bytes(rnd.randrange(256) for _ in range(w * h)), w, h)

    def test_uniform_planes(self):
        for t in (G_NONE, G_WALL, G_FLOOR, G_CEIL, G_SKY, G_THING, G_WEAPON):
            for d in (0, 5, 17, 31):
                self.check(bytes([t << 5 | d]) * (84 * 36), 84, 36)


if __name__ == "__main__":
    unittest.main()
