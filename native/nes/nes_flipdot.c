/* NES emulator (agnes, patched by agnes_flipdot.patch) for the flipdot board; apps/nes.py drives it over pipes.
 *
 *   nes_flipdot ROM.nes [-fps N] [-state FILE]
 *
 * Emulates at the NES's 60.1 frames per second and sends every (60/N)th frame, N = board updates per second.
 * After a button goes down or up it also sends every BOOST_STEP-th frame for BOOST_FRAMES frames, so the game's
 * response reaches the board in a few frames instead of waiting up to a whole 1/N s for the next regular send
 * (games take 1-4 frames to show a press, so one fixed extra send would sometimes come too early), then carries on
 * every 1/N s from there.
 *
 * stdout: one message per sent frame, 24-byte little-endian header, then
 *         256*240 bytes colour (NES palette index 0..63), 256*240 bytes layer (see agnes_flipdot.patch:
 *         bits 0-4 palette RAM address, 0x20 = opaque background, 0x40 = sprite behind the background,
 *         0x80 = sprite in front), 256 bytes OAM, 32 bytes palette RAM.
 *   0  "NES1"
 *   4  u16 width, u16 height
 *   8  u32 frame number (emulated frames)
 *   12 u16 scroll (PPU scroll mid-screen: changes when the game scrolls)
 *   14 u8 flags: 1 paused, 2 state saved, 4 state loaded, 8 no saved state, 16 saving failed,
 *                32 the saved state doesn't fit (another ROM or build)
 *   15 u8 mapper
 *   16 u32 CRC-32 of the ROM file
 *   20 u16 emulation time per frame in microseconds (average), u16 reserved
 * stdin: 3 bytes per message: command, player (1/2), button (0 a, 1 b, 2 select, 3 start, 4 up, 5 down, 6 left, 7 right)
 *   'D' button down, 'U' button up, 'S' save state, 'L' load state, 'P' pause / carry on. EOF quits.
 */

#include "agnes.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <time.h>
#include <unistd.h>

const uint8_t *agnes_fd_color(const agnes_t *a);
const uint8_t *agnes_fd_layer(const agnes_t *a);
const uint8_t *agnes_fd_oam(const agnes_t *a);
const uint8_t *agnes_fd_palette(const agnes_t *a);
uint16_t agnes_fd_scroll(const agnes_t *a);
int agnes_fd_mapper(const agnes_t *a);

#define W AGNES_SCREEN_WIDTH
#define H AGNES_SCREEN_HEIGHT
#define HEADER 24
#define NES_NS 16639267LL  /* 1 / 60.0988 Hz */
#define MIN_HOLD 3          /* frames a tap stays pressed, so a quick down+up still reaches the game */
#define BOOST_STEP 2        /* after input: send every 2nd frame (30 a second)... */
#define BOOST_FRAMES 8      /* ...for this many frames */

enum { F_PAUSED = 1, F_SAVED = 2, F_LOADED = 4, F_NOSAVE = 8, F_SAVEFAIL = 16, F_BADSAVE = 32 };

static unsigned char msg[HEADER + 2 * W * H + 256 + 32];
static agnes_t *ag;
static uint32_t rom_crc;
static const char *state_path;
static uint8_t held[2], early_up[2];
static uint32_t pressed_at[2][8];
static uint32_t frame;
static uint32_t boost_from, boost_end;  /* send often in (boost_from, boost_end]: just after a button changed */
static int paused, events;

static uint32_t crc32(const unsigned char *p, size_t n)
{
    uint32_t c = 0xffffffff;
    while (n--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xedb88320 & -(c & 1));
    }
    return ~c;
}

static long long now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static void put16(unsigned char *p, unsigned v) { p[0] = v & 0xff; p[1] = (v >> 8) & 0xff; }
static void put32(unsigned char *p, uint32_t v) { put16(p, v & 0xffff); put16(p + 2, v >> 16); }
static uint32_t get32(const unsigned char *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

static void write_all(const unsigned char *p, size_t n)
{
    while (n > 0) {
        ssize_t w = write(1, p, n);
        if (w < 0) {
            if (errno == EINTR) continue;
            exit(0);  /* the board app went away */
        }
        p += w;
        n -= w;
    }
}

/* saved state: "NESS", u32 version 1, u32 state size, u32 ROM CRC-32, then agnes's state */
static void save_state(void)
{
    if (!state_path) { events |= F_SAVEFAIL; return; }
    size_t n = agnes_state_size();
    unsigned char *buf = malloc(16 + n);
    if (!buf) { events |= F_SAVEFAIL; return; }
    memcpy(buf, "NESS", 4);
    put32(buf + 4, 1);
    put32(buf + 8, (uint32_t)n);
    put32(buf + 12, rom_crc);
    agnes_dump_state(ag, (agnes_state_t *)(buf + 16));
    char tmp[4096];
    snprintf(tmp, sizeof tmp, "%s.tmp", state_path);
    FILE *f = fopen(tmp, "wb");
    int ok = f && fwrite(buf, 1, 16 + n, f) == 16 + n;
    if (f && fclose(f) != 0) ok = 0;
    ok = ok && rename(tmp, state_path) == 0;
    if (!ok) { fprintf(stderr, "couldn't save %s: %s\n", state_path, strerror(errno)); unlink(tmp); }
    free(buf);
    events |= ok ? F_SAVED : F_SAVEFAIL;
}

static void load_state(void)
{
    FILE *f = state_path ? fopen(state_path, "rb") : NULL;
    if (!f) { events |= F_NOSAVE; return; }
    size_t n = agnes_state_size();
    unsigned char *buf = malloc(16 + n);
    int ok = buf && fread(buf, 1, 16 + n, f) == 16 + n && fgetc(f) == EOF && !memcmp(buf, "NESS", 4)
             && get32(buf + 4) == 1 && get32(buf + 8) == n && get32(buf + 12) == rom_crc;
    fclose(f);
    if (ok) agnes_restore_state(ag, (agnes_state_t *)(buf + 16));
    free(buf);
    events |= ok ? F_LOADED : F_BADSAVE;
}

static void read_input(void)
{
    static unsigned char in[96];
    static int len = 0;
    for (;;) {
        ssize_t n = read(0, in + len, sizeof in - len);
        if (n == 0) exit(0);  /* stdin closed: the board app stopped */
        if (n < 0) break;
        len += n;
        int i = 0;
        for (; i + 3 <= len; i += 3) {
            int cmd = in[i], p = in[i + 1] == 2 ? 1 : 0, b = in[i + 2] & 7;
            if (cmd == 'D' || cmd == 'U') {
                boost_from = frame;
                boost_end = frame + BOOST_FRAMES;
            }
            if (cmd == 'D') {
                held[p] |= 1 << b;
                early_up[p] &= ~(1 << b);
                pressed_at[p][b] = frame;
            } else if (cmd == 'U') {
                if (frame - pressed_at[p][b] < MIN_HOLD) early_up[p] |= 1 << b;
                else held[p] &= ~(1 << b);
            } else if (cmd == 'S') save_state();
            else if (cmd == 'L') load_state();
            else if (cmd == 'P') paused = !paused;
        }
        memmove(in, in + i, len - i);
        len -= i;
    }
}

static void set_input(void)
{
    agnes_input_t in[2];
    for (int p = 0; p < 2; p++) {
        for (int b = 0; b < 8; b++)  /* taps released early: let go once they've been down long enough */
            if (early_up[p] & 1 << b && frame - pressed_at[p][b] >= MIN_HOLD) {
                held[p] &= ~(1 << b);
                early_up[p] &= ~(1 << b);
            }
        uint8_t h = held[p];
        in[p] = (agnes_input_t){ .a = h & 1, .b = h >> 1 & 1, .select = h >> 2 & 1, .start = h >> 3 & 1,
                                 .up = h >> 4 & 1, .down = h >> 5 & 1, .left = h >> 6 & 1, .right = h >> 7 & 1 };
        if (in[p].up && in[p].down) in[p].down = 0;  /* a real pad can't press both: some games break */
        if (in[p].left && in[p].right) in[p].right = 0;
    }
    agnes_set_input(ag, &in[0], &in[1]);
}

static void send_frame(unsigned emu_us)
{
    memcpy(msg, "NES1", 4);
    put16(msg + 4, W);
    put16(msg + 6, H);
    put32(msg + 8, frame);
    put16(msg + 12, agnes_fd_scroll(ag));
    msg[14] = (paused ? F_PAUSED : 0) | events;
    msg[15] = agnes_fd_mapper(ag);
    put32(msg + 16, rom_crc);
    put16(msg + 20, emu_us > 65535 ? 65535 : emu_us);
    put16(msg + 22, 0);
    unsigned char *o = msg + HEADER;
    memcpy(o, agnes_fd_color(ag), W * H);
    memcpy(o + W * H, agnes_fd_layer(ag), W * H);
    memcpy(o + 2 * W * H, agnes_fd_oam(ag), 256);
    memcpy(o + 2 * W * H + 256, agnes_fd_palette(ag), 32);
    write_all(msg, sizeof msg);
    events = 0;
}

int main(int argc, char **argv)
{
    const char *rom_path = NULL;
    int fps = 10;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-fps") && i + 1 < argc) fps = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-state") && i + 1 < argc) state_path = argv[++i];
        else rom_path = argv[i];
    }
    if (!rom_path) { fprintf(stderr, "usage: nes_flipdot ROM.nes [-fps N] [-state FILE]\n"); return 2; }
    if (fps < 1) fps = 1;
    if (fps > 60) fps = 60;
    prctl(PR_SET_PDEATHSIG, SIGKILL);  /* never outlive the server */
    signal(SIGPIPE, SIG_DFL);
    fcntl(0, F_SETFL, fcntl(0, F_GETFL) | O_NONBLOCK);

    FILE *f = fopen(rom_path, "rb");
    if (!f) { fprintf(stderr, "can't open %s: %s\n", rom_path, strerror(errno)); return 1; }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    rewind(f);
    if (size < 16 || size > 8 << 20) { fprintf(stderr, "%s isn't an iNES ROM\n", rom_path); return 1; }
    unsigned char *rom = malloc(size);  /* agnes keeps pointing into it */
    if (!rom || fread(rom, 1, size, f) != (size_t)size) { fprintf(stderr, "can't read %s\n", rom_path); return 1; }
    fclose(f);
    rom_crc = crc32(rom, size);
    int mapper = (rom[6] >> 4) | (rom[7] & 0xf0);
    ag = agnes_make();
    if (!ag || !agnes_load_ines_data(ag, rom, size)) {
        if (size >= 16 && !memcmp(rom, "NES\x1a", 4) && mapper != 0 && mapper != 1 && mapper != 2 && mapper != 3
            && mapper != 4 && mapper != 7) {
            fprintf(stderr, "unsupported mapper %d\n", mapper);
            return 3;
        }
        fprintf(stderr, "%s isn't a ROM this emulator can load\n", rom_path);
        return 1;
    }

    long long period = NES_NS, send_every = 1000000000LL / fps;
    long long next = now_ns(), next_send = next, emu_ns = 0;
    uint32_t last_sent = 0;
    int emu_n = 0;
    unsigned emu_us = 0;
    for (;;) {
        read_input();
        if (paused) {
            if (events) send_frame(emu_us);  /* acknowledge a save/load made while paused */
            struct timespec ts = { 0, 20000000 };
            nanosleep(&ts, NULL);
            next = next_send = now_ns();
            continue;
        }
        set_input();
        long long t0 = now_ns();
        agnes_next_frame(ag);
        long long t1 = now_ns();
        frame++;
        emu_ns += t1 - t0;
        if (++emu_n == 60) {
            emu_us = (unsigned)(emu_ns / 60000);
            emu_ns = 0;
            emu_n = 0;
        }
        int boosting = boost_end && frame <= boost_end;
        if (t1 >= next_send || events
            || (boosting && frame >= boost_from + BOOST_STEP && frame - last_sent >= BOOST_STEP)) {
            send_frame(emu_us);
            last_sent = frame;
            /* while boosting, the regular sends line up behind the last quick one */
            next_send = boosting ? t1 + send_every : next_send + send_every;
            if (next_send < t1) next_send = t1 + send_every;
        }
        if (boost_end && frame > boost_end) boost_end = 0;
        next += period;
        if (next < t1 - 5 * period) next = t1;  /* fell far behind (a slow moment): don't race to catch up */
        struct timespec ts = { next / 1000000000LL, next % 1000000000LL };
        while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL) == EINTR) {}
    }
}
