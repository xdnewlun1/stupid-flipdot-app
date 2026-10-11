"""Timings of the per-frame paths (board framebuffer, transitions, video, music analysis). Not a test: run it on the Pi to see where
the time goes, and before/after a change. Run: python3 tests/bench_core.py"""

import logging
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

import audio_signal  # noqa: E402
import flipdot  # noqa: E402
import transitions  # noqa: E402
import video  # noqa: E402
from canvas import Canvas  # noqa: E402
from transitions import Transition  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def t(name, fn, n=200):
    fn()
    s = time.perf_counter()
    for _ in range(n):
        fn()
    print(f"{name:44s} {(time.perf_counter() - s) / n * 1e3:8.3f} ms")


def main():
    board = flipdot.Board(flipdot.load_config(os.path.join(ROOT, "config.yaml")), dry_run=True)
    W, H = board.width, board.height
    rnd = random.Random(1)
    pics = []
    for _ in range(4):
        c = Canvas(W, H)
        c.px = bytearray(rnd.getrandbits(1) for _ in range(W * H))
        pics.append(c)

    print("board")
    t("set_canvas, same picture", lambda: board.set_canvas(pics[0]))
    k = [0]

    def changed():
        k[0] ^= 1
        board.set_canvas(pics[k[0]])
    t("set_canvas, every dot new", changed)
    _, px = board.dots()
    t("panel frames, all panels", lambda: [board._frame_for(p, px) for p in board.panels])

    def packed():
        board._packed = (None, b"")
        board.packed()
    t("packed() (uncached)", packed)
    t("rows()", board.rows)

    print("transitions")
    t("build a dissolve", lambda: Transition("dissolve", pics[0], W, H), 50)
    for kind in ("dissolve", "morph", "life", "scatter", "push-left", "zoom-in"):
        tr = Transition(kind, pics[1], W, H)

        def mix():
            transitions._df_cache.clear()
            tr.mix(pics[2], tr.start + tr.duration / 2)
        t(f"{kind}, one frame", mix, 20)

    print("video")
    n = W * H
    frames = [bytes(rnd.getrandbits(8) for _ in range(n)) for _ in range(video.CHUNK)]
    data = video._delta(frames)
    t("encode a chunk (delta)", lambda: video._delta(frames), 20)
    t("decode a chunk (undelta)", lambda: video._undelta(data, n), 20)
    for dither in ("ordered", "diffusion", "threshold"):
        d = video.display_opts({"dither": dither})
        t(f"to_dots {dither}", lambda: video.to_dots(frames[0], W, H, d))

    print("music analysis")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import music_signals
    sig = [s for _, s in music_signals.song("funk").signal][::2]  # 25 readings a second, as the board reads it
    proc, clock, k = audio_signal.Processor(), [1000.0], [0]

    def step():
        proc.step(sig[k[0] % len(sig)], clock[0])
        k[0] += 1
        clock[0] += 0.04
    t("audio_signal reading (funk, mean)", step, len(sig) * 2)


if __name__ == "__main__":
    main()
