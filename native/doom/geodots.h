/* The board's dots from Doom's geometry plane, in C (a port of apps/doom.py's draw_geometry).
 *
 * The geometry plane has one byte per pixel: surface type in the top 3 bits, distance (log scale, 0 = touching,
 * 31 = far) in the low 5. geo_dots() turns a board-sized plane into 0/1 dots: walls dithered by distance (near =
 * dense, far = sparse) or left empty (wireframe), floors/ceilings/sky dark, an outline wherever surfaces meet or
 * one wall stands in front of another, monsters/items solid with a dark ring, and the weapon as an outline.
 *
 * Header-only, with no Doom dependencies, so tests/test_doom_geo.py can compile it on its own and compare it with
 * the Python version dot for dot. Keep the two in step.
 */
#ifndef GEODOTS_H
#define GEODOTS_H

enum { GD_NONE, GD_WALL, GD_FLOOR, GD_CEIL, GD_SKY, GD_THING, GD_WEAPON };

static const unsigned char GD_BAYER4[16] = {0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5};

/* work bits while the picture is built (out[] doubles as the scratch buffer) */
#define GD_DOT 1
#define GD_LIT 2
#define GD_DARK 4

/* Nearest-neighbour sample: the source column (of sw) for output column x (of dw), taken at the pixel's centre. */
static inline int gd_nearest(int x, int sw, int dw)
{
    return (int)(((2L * x + 1) * sw) / (2L * dw));
}

/* Shrink a geometry plane to w*h: `sw` columns `xstep` bytes apart and `sh` rows `stride` bytes apart are the
 * source (so a 2x2-subsampled view of the engine's plane is stride 2*RESX, xstep 2). */
static inline void geo_sample(const unsigned char *geo, long stride, int xstep, int sw, int sh, unsigned char *out, int w, int h)
{
    for (int y = 0; y < h; y++) {
        const unsigned char *row = geo + gd_nearest(y, sh, h) * stride;
        for (int x = 0; x < w; x++) *out++ = row[gd_nearest(x, sw, w) * xstep];
    }
}

static inline void geo_dots(const unsigned char *geo, int w, int h, int wireframe, unsigned char *out)
{
    int n = w * h;
    for (int i = 0; i < n; i++) {
        unsigned char v = 0;
        if (!wireframe && (geo[i] >> 5) == GD_WALL) {
            int y = i / w, x = i % w;
            v = 236 - (geo[i] & 31) * 7 > GD_BAYER4[(y & 3) * 4 + (x & 3)] * 16 + 8 ? GD_DOT : 0;
        }
        out[i] = v;
    }
    for (int i = 0; i < n; i++) {
        int t = geo[i] >> 5, d = geo[i] & 31, x = i % w;
        int js[2], nj = 0;
        if (x < w - 1) js[nj++] = i + 1;
        if (i + w < n) js[nj++] = i + w;
        for (int k = 0; k < nj; k++) {
            int j = js[k], u = geo[j] >> 5, e = geo[j] & 31;
            if (t == u && (t != GD_WALL || (d > e ? d - e : e - d) < 3)) continue;
            if (t == GD_THING || u == GD_THING) { /* dark ring around monsters/items so they stand out */
                if (t != GD_THING) out[i] |= GD_DARK;
                if (u != GD_THING) out[j] |= GD_DARK;
            } else if (t == GD_WEAPON || u == GD_WEAPON) {
                out[t == GD_WEAPON ? i : j] |= GD_LIT;
            } else if (t == u) { /* one wall in front of another: light the near edge, gap behind it */
                int near = d < e ? i : j, far = d < e ? j : i;
                out[near] |= GD_LIT;
                out[far] |= GD_DARK;
            } else if (t == GD_WALL || u == GD_WALL) { /* where a wall meets floor/ceiling/sky */
                out[t == GD_WALL ? i : j] |= GD_LIT;
            } else if (t != GD_NONE && u != GD_NONE) { /* floor meeting ceiling (a closing door), steps... */
                out[d <= e ? i : j] |= GD_LIT;
            }
        }
    }
    for (int i = 0; i < n; i++) {
        int t = geo[i] >> 5, v = out[i];
        int dot = v & GD_DOT;
        if (t == GD_THING) dot = 1;
        else if (t == GD_WEAPON) dot = 0;
        if (v & GD_DARK) dot = 0;
        if ((v & GD_LIT) && (!(v & GD_DARK) || t == GD_WEAPON)) dot = 1;
        out[i] = dot;
    }
}

#endif
