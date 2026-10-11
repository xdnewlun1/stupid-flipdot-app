/* The few per-dot algorithms that have no slice/bytes/Pillow formulation, in C (loaded by fastpath.py through
 * ctypes; fastpath.py builds this file with the system C compiler the first time and falls back to its pure-Python
 * versions of the same functions when there's no compiler). Each function here must give exactly the same result as
 * its Python twin in fastpath.py: tests/test_fast.py checks that.
 *
 * Pictures are one byte per dot, 0 or 1, row-major, as everywhere in the server (canvas.Canvas.px).
 */

#include <stdint.h>
#include <stdlib.h>

#define INF 1000000

/* Chamfer 3-4 distance from every dot to the nearest dot equal to `target`, two sequential passes over a grid with a
 * one-dot INF border. d is (w+2)*(h+2). */
static void chamfer(const uint8_t *px, int w, int h, uint8_t target, int32_t *d)
{
    int W2 = w + 2;
    for (int i = 0; i < W2 * (h + 2); i++) d[i] = INF;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) d[(y + 1) * W2 + x + 1] = px[y * w + x] == target ? 0 : INF;
    for (int y = 1; y <= h; y++) {
        for (int x = 1; x <= w; x++) {
            int i = y * W2 + x, b = d[i], t;
            if (!b) continue;
            t = d[i - 1] + 3; if (t < b) b = t;
            t = d[i - W2] + 3; if (t < b) b = t;
            t = d[i - W2 - 1] + 4; if (t < b) b = t;
            t = d[i - W2 + 1] + 4; if (t < b) b = t;
            d[i] = b;
        }
    }
    for (int y = h; y >= 1; y--) {
        for (int x = w; x >= 1; x--) {
            int i = y * W2 + x, b = d[i], t;
            if (!b) continue;
            t = d[i + 1] + 3; if (t < b) b = t;
            t = d[i + W2] + 3; if (t < b) b = t;
            t = d[i + W2 + 1] + 4; if (t < b) b = t;
            t = d[i + W2 - 1] + 4; if (t < b) b = t;
            d[i] = b;
        }
    }
}

/* Signed chamfer distance to the white/black boundary: positive inside white areas (distance to the nearest black
 * dot), negative inside black ones. In chamfer units (3 per straight step, 4 per diagonal). */
void fast_distance_field(const uint8_t *px, int w, int h, int32_t *out)
{
    int W2 = w + 2;
    int32_t *a = malloc(sizeof(int32_t) * W2 * (h + 2)), *b = malloc(sizeof(int32_t) * W2 * (h + 2));
    if (!a || !b) { free(a); free(b); return; }
    chamfer(px, w, h, 0, a);
    chamfer(px, w, h, 1, b);
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            int i = y * w + x, j = (y + 1) * W2 + x + 1;
            out[i] = px[i] ? a[j] : -b[j];
        }
    free(a);
    free(b);
}

/* The morph: a dot is white where the blend of the two fields is positive. */
void fast_morph_blend(const int32_t *a, const int32_t *b, int n, double p, uint8_t *out)
{
    double q = 1.0 - p;
    for (int i = 0; i < n; i++) out[i] = q * a[i] + p * b[i] > 0 ? 1 : 0;
}

/* One generation of Conway's Life on a torus (the edges wrap). */
void fast_life_step(const uint8_t *cells, int w, int h, uint8_t *out)
{
    for (int y = 0; y < h; y++) {
        const uint8_t *up = cells + ((y + h - 1) % h) * w, *row = cells + y * w, *dn = cells + ((y + 1) % h) * w;
        for (int x = 0; x < w; x++) {
            int l = (x + w - 1) % w, r = (x + 1) % w;
            int n = up[l] + up[x] + up[r] + row[l] + row[r] + dn[l] + dn[x] + dn[r];
            out[y * w + x] = (n == 3 || (n == 2 && row[x])) ? 1 : 0;
        }
    }
}
