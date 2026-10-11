/* doomgeneric platform for the flipdot board (apps/doom.py drives it over pipes).
 *
 * stdout: one message per frame, 24-byte little-endian header + w*h bytes of 8-bit gray
 *         + w*h bytes of geometry (see "what each pixel is" below)
 *   0  "FDM2"
 *   4  u16 width, u16 height          (half of Doom's 320x200: 2x2 boxes averaged)
 *   8  i16 health, i16 armor, i16 ammo (ammo of the current weapon, -1 for fist/chainsaw)
 *   14 u8 weapon (0 fist .. 8 super shotgun), u8 flags (1 = in a level, 2 = dead, 4 = menu open,
 *      8 = automap, 16 = the geometry plane is valid this frame)
 *   16 u16 owned weapons (bit per weapon), u16 view rows (rows above the status bar)
 *   20 u32 frame number
 * stdin: 2 bytes per key event: pressed (0/1), Doom key code. EOF quits.
 * Doom's own console output goes to stderr.
 *
 * With -flipdots W H LOOK (LOOK: outline or wireframe) the 3D view is turned into the board's dots here
 * (geodots.h) and sent as "FDM3": the same header with width W and height H, then W*H bytes of 0/1 dots. That
 * saves the Python side a per-dot pass over the picture on every frame and cuts the frame from 32 KB to W*H bytes.
 * Menus and the automap still go out as FDM2 (gray + geometry), since they're drawn from the gray picture.
 *
 * Extra arguments: -flipfps N  frames per second to render and send (default 10).
 * After a key goes down or up it also renders and sends every tic (35 a second) for BOOST_MS, so the response
 * reaches the board in a tic or two instead of waiting up to a whole 1/N s; then it carries on every 1/N s.
 */

#include "doomgeneric.h"
#include "doomkeys.h"
#include "doomstat.h"
#include "d_items.h"
#include "d_player.h"
#include "i_video.h"
#include "m_argv.h"
#include "m_controls.h"
#include "r_state.h"
#include "r_draw.h"
#include "r_main.h"
#include "r_sky.h"
#include "r_things.h"

#include <errno.h>
#include <math.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <time.h>
#include <unistd.h>

#include "geodots.h"

#define OUT_W (DOOMGENERIC_RESX / 2)
#define OUT_H (DOOMGENERIC_RESY / 2)
#define HEADER 24
#define STATUSBAR_H 32
#define BOOST_MS 150     /* after a key event: send every tic for this long... */
#define BOOST_GAP_MS 25  /* ...(at most this often: a tic is 28.6 ms) */

extern int showMessages;
extern int screenblocks;

static int out_fd = -1;
static uint32_t frame_ms = 100, next_due = 0, frames = 0;
static uint32_t boost_until = 0;  /* send every tic until then (just after a key event)... */
static int boosting = 0;          /* ...while this is set (the ms clock wraps, so 0 can't mean "off") */
static unsigned char msg[HEADER + 2 * OUT_W * OUT_H];
static int dots_w = 0, dots_h = 0, dots_wire = 0;  /* -flipdots: make the board's dots here (0 = send gray + geometry) */
static unsigned char inbuf[64];
static int inlen = 0;

/* ---- what each pixel is, and how far away -------------------------------------------------
 * At 84x42 a far wall and the floor dither to the same gray, so we also send a "geometry" plane:
 * one byte per pixel, surface type in the top 3 bits and distance (log scale, 0 = touching,
 * 31 = far) in the low 5. We get it by wrapping the renderer's column/span drawers: walls are
 * drawn as columns before any floor span, then floors/ceilings as spans (and the sky as columns
 * using the sky's texture offset), then sprites as columns, the player's weapon last with no
 * ceiling clip. Each drawer's step size is the distance to what it draws. */
enum { G_NONE, G_WALL, G_FLOOR, G_CEIL, G_SKY, G_THING, G_WEAPON };
static unsigned char geo[DOOMGENERIC_RESX * DOOMGENERIC_RESY];
static int seen_span = 0;
static void (*real_col)(void), (*real_fuzz)(void), (*real_trans)(void), (*real_span)(void);

static unsigned char depth_code(uint32_t dist)  /* map units -> 0..31, 4 steps per doubling */
{
    if (dist <= 16) return 0;  /* closer than 16 units counts as touching */
    int q = (int)(4 * log2f(dist / 16.0f) + 0.5f);
    return q > 31 ? 31 : q;
}

static void mark_column(void)
{
    int type;
    if (dc_texturemid == skytexturemid && dc_colormap == colormaps && dc_iscale == pspriteiscale >> detailshift) {
        type = G_SKY;  /* exactly how R_DrawPlanes sets up a sky column */
        seen_span = 1;  /* planes are being drawn: walls are done */
    } else if (!seen_span) type = G_WALL;
    else type = mceilingclip == negonearray ? G_WEAPON : G_THING;
    uint32_t dist = (uint32_t)(((uint64_t)(uint32_t)abs(dc_iscale) * centerx) >> FRACBITS) << detailshift;
    unsigned char v = type << 5 | (type == G_SKY ? 31 : depth_code(dist));
    int w = 1 << detailshift, x0 = viewwindowx + (dc_x << detailshift);
    for (int y = dc_yl; y <= dc_yh; y++) {
        int yy = viewwindowy + y;
        if (yy < 0 || yy >= DOOMGENERIC_RESY) continue;
        for (int k = 0; k < w && x0 + k < DOOMGENERIC_RESX; k++) geo[yy * DOOMGENERIC_RESX + x0 + k] = v;
    }
}

static void col_hook(void) { mark_column(); real_col(); }
static void fuzz_hook(void) { mark_column(); real_fuzz(); }
static void trans_hook(void) { mark_column(); real_trans(); }

static void span_hook(void)
{
    seen_span = 1;
    int yy = viewwindowy + ds_y;
    if (yy >= 0 && yy < DOOMGENERIC_RESY) {
        double sx = ds_xstep, sy = ds_ystep;  /* = distance / centerx, along the view's x axis */
        uint32_t dist = (uint32_t)(__builtin_sqrt(sx * sx + sy * sy) * centerx / FRACUNIT) << detailshift;
        unsigned char v = (ds_y > centery ? G_FLOOR : G_CEIL) << 5 | depth_code(dist);
        int x1 = viewwindowx + (ds_x1 << detailshift), x2 = viewwindowx + ((ds_x2 + 1) << detailshift) - 1;
        for (int x = x1 < 0 ? 0 : x1; x <= x2 && x < DOOMGENERIC_RESX; x++) geo[yy * DOOMGENERIC_RESX + x] = v;
    }
    real_span();
}

static void hook_renderer(void)
{
    /* R_ExecuteSetViewSize (screen size / detail changes) puts the stock drawers back */
    if (basecolfunc != col_hook && basecolfunc) {
        real_col = basecolfunc;
        if (colfunc == basecolfunc) colfunc = col_hook;
        basecolfunc = col_hook;
    }
    if (fuzzcolfunc != fuzz_hook && fuzzcolfunc) { real_fuzz = fuzzcolfunc; fuzzcolfunc = fuzz_hook; }
    if (transcolfunc != trans_hook && transcolfunc) { real_trans = transcolfunc; transcolfunc = trans_hook; }
    if (spanfunc != span_hook && spanfunc) { real_span = spanfunc; spanfunc = span_hook; }
}

static void put16(unsigned char *p, int v) { p[0] = v & 0xff; p[1] = (v >> 8) & 0xff; }

static void write_all(const unsigned char *p, size_t n)
{
    while (n > 0) {
        ssize_t w = write(out_fd, p, n);
        if (w < 0) {
            if (errno == EINTR) continue;
            exit(0);  /* the board app went away */
        }
        p += w;
        n -= w;
    }
}

void DG_Init()
{
    /* keep stdout for frames; everything Doom prints goes to stderr */
    out_fd = dup(1);
    dup2(2, 1);
    setvbuf(stdout, NULL, _IONBF, 0);  /* keeps it in order with stderr, so errors come last */
    fcntl(0, F_SETFL, fcntl(0, F_GETFL) | O_NONBLOCK);
    prctl(PR_SET_PDEATHSIG, SIGKILL);  /* never outlive the server */
    signal(SIGPIPE, SIG_DFL);

    next_due = DG_GetTicksMs();  /* not 0: the ms clock passes 2^31 after ~25 days up, and 0 would look far ahead */

    int i = M_CheckParmWithArgs("-flipfps", 1);
    if (i > 0) {
        int fps = atoi(myargv[i + 1]);
        if (fps >= 1 && fps <= 35) frame_ms = 1000 / fps;
    }
    i = M_CheckParmWithArgs("-flipdots", 3);
    if (i > 0) {
        int w = atoi(myargv[i + 1]), h = atoi(myargv[i + 2]);
        if (w >= 1 && h >= 1 && w * h <= 2 * OUT_W * OUT_H) {  /* the dots must fit the message buffer */
            dots_w = w;
            dots_h = h;
            dots_wire = strcmp(myargv[i + 3], "wireframe") == 0;
        }
    }
}

void DG_DrawFrame()
{
    static int configured = 0;
    if (!configured) {
        /* after the config file is loaded: no text messages (unreadable at 84x42),
           and a key for "next weapon" (unbound in vanilla) */
        showMessages = 0;
        key_nextweapon = KEY_PGUP;
        key_prevweapon = KEY_PGDN;
        configured = 1;
    }

    hook_renderer();
    int had_3d = seen_span;
    seen_span = 0;  /* the next frame starts with walls again */

    uint32_t now = DG_GetTicksMs();
    if ((int32_t)(now - next_due) < 0) { memset(geo, 0, sizeof geo); return; }
    next_due = (now - next_due > frame_ms * 2) ? now + frame_ms : next_due + frame_ms;
    if (boosting) {
        if ((int32_t)(boost_until - (now + BOOST_GAP_MS)) > 0) {
            next_due = now + BOOST_GAP_MS;  /* still right after input: the next tic goes out too */
        } else {
            next_due = now + frame_ms;  /* the last quick one: the regular sends line up behind it */
            boosting = 0;
        }
    }

    player_t *pl = &players[consoleplayer];
    int in_level = gamestate == GS_LEVEL;
    int view = (in_level && screenblocks <= 10) ? (DOOMGENERIC_RESY - STATUSBAR_H) / 2 : OUT_H;
    /* the 3D view as the board's dots, straight from the geometry (the picture apps/doom.py made from FDM2 itself) */
    int dots = dots_w && had_3d && !menuactive && !automapactive;
    unsigned char *o = msg + HEADER;
    if (dots) {
        static unsigned char small[2 * OUT_W * OUT_H];
        /* as the Python side did: the top-left of each 2x2 (types can't be averaged), rows above the status bar,
           then nearest-neighbour down to the board */
        geo_sample(geo, 2 * DOOMGENERIC_RESX, 2, OUT_W, view, small, dots_w, dots_h);
        geo_dots(small, dots_w, dots_h, dots_wire, o);
        o += dots_w * dots_h;
    } else {
        /* 2x2 box average to 8-bit gray */
        for (int y = 0; y < OUT_H; y++) {
            const uint32_t *r0 = DG_ScreenBuffer + (2 * y) * DOOMGENERIC_RESX;
            const uint32_t *r1 = r0 + DOOMGENERIC_RESX;
            for (int x = 0; x < OUT_W; x++) {
                uint32_t s = 0;
                for (int k = 0; k < 4; k++) {
                    uint32_t p = k == 0 ? r0[2 * x] : k == 1 ? r0[2 * x + 1] : k == 2 ? r1[2 * x] : r1[2 * x + 1];
                    s += ((p >> 16) & 0xff) * 77 + ((p >> 8) & 0xff) * 150 + (p & 0xff) * 29;
                }
                *o++ = s >> 10;
            }
        }
        /* geometry plane: the top-left sample of each 2x2 (types can't be averaged) */
        for (int y = 0; y < OUT_H; y++)
            for (int x = 0; x < OUT_W; x++)
                *o++ = geo[(2 * y) * DOOMGENERIC_RESX + 2 * x];
    }
    memset(geo, 0, sizeof geo);

    int ammo = -1, owned = 0;
    if (in_level) {
        ammotype_t at = weaponinfo[pl->readyweapon].ammo;
        if (at != am_noammo) ammo = pl->ammo[at];
        for (int w = 0; w < NUMWEAPONS; w++)
            if (pl->weaponowned[w]) owned |= 1 << w;
    }

    memcpy(msg, dots ? "FDM3" : "FDM2", 4);
    put16(msg + 4, dots ? dots_w : OUT_W);
    put16(msg + 6, dots ? dots_h : OUT_H);
    put16(msg + 8, in_level ? pl->health : 0);
    put16(msg + 10, in_level ? pl->armorpoints : 0);
    put16(msg + 12, ammo);
    msg[14] = in_level ? pl->readyweapon : 0;
    msg[15] = (in_level ? 1 : 0) | (in_level && pl->playerstate == PST_DEAD ? 2 : 0)
              | (menuactive ? 4 : 0) | (automapactive ? 8 : 0) | (had_3d ? 16 : 0);
    put16(msg + 16, owned);
    put16(msg + 18, view);
    frames++;
    put16(msg + 20, frames & 0xffff);
    put16(msg + 22, frames >> 16);
    write_all(msg, o - msg);
}

void DG_SleepMs(uint32_t ms)
{
    usleep(ms * 1000);
}

uint32_t DG_GetTicksMs()
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}

int DG_GetKey(int *pressed, unsigned char *key)
{
    if (inlen < 2) {
        ssize_t n = read(0, inbuf + inlen, sizeof inbuf - inlen);
        if (n == 0) exit(0);  /* stdin closed: the board app stopped */
        if (n > 0) inlen += n;
    }
    if (inlen < 2) return 0;
    *pressed = inbuf[0] ? 1 : 0;
    *key = inbuf[1];
    uint32_t now = DG_GetTicksMs();
    boost_until = now + BOOST_MS;
    boosting = 1;
    if ((int32_t)(next_due - (now + BOOST_GAP_MS)) > 0)
        next_due = now + BOOST_GAP_MS;  /* render the tic that responds to it, not the one 1/N s later */
    memmove(inbuf, inbuf + 2, inlen - 2);
    inlen -= 2;
    return 1;
}

void DG_SetWindowTitle(const char *title)
{
    (void)title;
}

int main(int argc, char **argv)
{
    doomgeneric_Create(argc, argv);
    for (;;) {
        /* only draw the frames we send: saves most of the rendering CPU */
        screenvisible = (int32_t)(DG_GetTicksMs() - next_due) >= 0;
        doomgeneric_Tick();
    }
    return 0;
}
