/* doomgeneric platform for the flipdot board (apps/doom.py drives it over pipes).
 *
 * stdout: one message per frame, 24-byte little-endian header + w*h bytes of 8-bit gray
 *   0  "FDM1"
 *   4  u16 width, u16 height          (half of Doom's 320x200: 2x2 boxes averaged)
 *   8  i16 health, i16 armor, i16 ammo (ammo of the current weapon, -1 for fist/chainsaw)
 *   14 u8 weapon (0 fist .. 8 super shotgun), u8 flags (1 = in a level, 2 = dead)
 *   16 u16 owned weapons (bit per weapon), u16 view rows (rows above the status bar)
 *   20 u32 frame number
 * stdin: 2 bytes per key event: pressed (0/1), Doom key code. EOF quits.
 * Doom's own console output goes to stderr.
 *
 * Extra arguments: -flipfps N  frames per second to render and send (default 10).
 */

#include "doomgeneric.h"
#include "doomkeys.h"
#include "doomstat.h"
#include "d_items.h"
#include "d_player.h"
#include "i_video.h"
#include "m_argv.h"
#include "m_controls.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <time.h>
#include <unistd.h>

#define OUT_W (DOOMGENERIC_RESX / 2)
#define OUT_H (DOOMGENERIC_RESY / 2)
#define HEADER 24
#define STATUSBAR_H 32

extern int showMessages;
extern int screenblocks;

static int out_fd = -1;
static uint32_t frame_ms = 100, next_due = 0, frames = 0;
static unsigned char msg[HEADER + OUT_W * OUT_H];
static unsigned char inbuf[64];
static int inlen = 0;

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

    int i = M_CheckParmWithArgs("-flipfps", 1);
    if (i > 0) {
        int fps = atoi(myargv[i + 1]);
        if (fps >= 1 && fps <= 35) frame_ms = 1000 / fps;
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

    uint32_t now = DG_GetTicksMs();
    if ((int32_t)(now - next_due) < 0) return;
    next_due = (now - next_due > frame_ms * 2) ? now + frame_ms : next_due + frame_ms;

    /* 2x2 box average to 8-bit gray */
    unsigned char *o = msg + HEADER;
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

    player_t *pl = &players[consoleplayer];
    int in_level = gamestate == GS_LEVEL;
    int ammo = -1, owned = 0;
    if (in_level) {
        ammotype_t at = weaponinfo[pl->readyweapon].ammo;
        if (at != am_noammo) ammo = pl->ammo[at];
        for (int w = 0; w < NUMWEAPONS; w++)
            if (pl->weaponowned[w]) owned |= 1 << w;
    }
    int view = (in_level && screenblocks <= 10) ? (DOOMGENERIC_RESY - STATUSBAR_H) / 2 : OUT_H;

    memcpy(msg, "FDM1", 4);
    put16(msg + 4, OUT_W);
    put16(msg + 6, OUT_H);
    put16(msg + 8, in_level ? pl->health : 0);
    put16(msg + 10, in_level ? pl->armorpoints : 0);
    put16(msg + 12, ammo);
    msg[14] = in_level ? pl->readyweapon : 0;
    msg[15] = (in_level ? 1 : 0) | (in_level && pl->playerstate == PST_DEAD ? 2 : 0);
    put16(msg + 16, owned);
    put16(msg + 18, view);
    frames++;
    put16(msg + 20, frames & 0xffff);
    put16(msg + 22, frames >> 16);
    write_all(msg, sizeof msg);
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
