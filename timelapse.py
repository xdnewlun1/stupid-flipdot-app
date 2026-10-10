"""Records what the board shows (in memory) and renders it as an animated GIF."""

import io
import threading
import time
from collections import deque

MAX_SECONDS = 30 * 60
MAX_BYTES = 4_000_000
MIN_INTERVAL = 0.1  # at most 10 snapshots a second
MAX_GIF_FRAMES = 1500
GIF_FRAME_S = 0.05  # 20 fps output (browsers slow down anything faster than 50 fps)

COLORS = [(5, 5, 5), (17, 17, 17), (36, 36, 36), (214, 206, 186), (255, 251, 240)]  # board, off rim/face, on rim/face


class Recorder:
    """Ring buffer of (time, packed bits). Each row is packed MSB-first and padded to whole bytes,
    which is exactly Pillow's "1" raw format."""

    def __init__(self, board):
        self.board = board
        self.w, self.h = board.width, board.height
        self.row_bytes = (self.w + 7) // 8
        self.frames = deque()
        self.size = 0
        self._lock = threading.Lock()
        self._event = threading.Event()
        board.listeners.append(self._event.set)
        self._snapshot()
        threading.Thread(target=self._loop, daemon=True).start()

    def _pack(self, rows):
        pad = self.row_bytes * 8 - self.w
        return b"".join((int(r, 2) << pad).to_bytes(self.row_bytes, "big") for r in rows)

    def _snapshot(self):
        data = self._pack(self.board.rows())
        now = time.time()
        with self._lock:
            if self.frames and self.frames[-1][1] == data:
                return
            self.frames.append((now, data))
            self.size += len(data) + 64  # rough per-entry overhead
            # keep one frame older than the window so a timelapse can start with the picture already up
            while len(self.frames) > 2 and (self.size > MAX_BYTES or self.frames[1][0] < now - MAX_SECONDS):
                self.size -= len(self.frames.popleft()[1]) + 64
            if self.frames[0][0] < now - MAX_SECONDS:
                self.frames[0] = (now - MAX_SECONDS, self.frames[0][1])

    def _loop(self):
        while True:
            self._event.wait()
            self._event.clear()
            try:
                self._snapshot()
            except Exception:
                pass
            time.sleep(MIN_INTERVAL)

    def info(self):
        with self._lock:
            span = time.time() - self.frames[0][0] if self.frames else 0
            return {"seconds": round(span), "frames": len(self.frames), "bytes": self.size}

    def window(self, seconds):
        """Frames covering the last `seconds`: the picture at the start, then each change."""
        now = time.time()
        start = now - seconds
        with self._lock:
            frames = list(self.frames)
        first = 0
        for i, (t, _) in enumerate(frames):
            if t <= start:
                first = i
        frames = frames[first:]
        start = max(start, frames[0][0])
        return [(max(t, start), d) for t, d in frames], start, now


def _disc(cell, lit):
    """One antialiased disc tile, drawn big and scaled down (shaded like the web preview)."""
    from PIL import Image, ImageDraw
    big = cell * 8
    im = Image.new("RGB", (big, big), COLORS[0])
    d = ImageDraw.Draw(im)
    rim, face = (COLORS[3], COLORS[4]) if lit else (COLORS[1], COLORS[2])
    m, r = big / 2, big * 0.43
    d.ellipse((m - r, m - r, m + r, m + r), fill=rim)
    rr, f = r * 0.7, m - r * 0.15
    d.ellipse((f - rr, f - rr, f + rr, f + rr), fill=face)
    return im.resize((cell, cell), Image.LANCZOS)


def _boards(w, h, cell):
    """Two 'P' images (one shared palette) of the whole board: every disc off, every disc lit."""
    from PIL import Image
    both = Image.new("RGB", (w * cell, h * cell * 2))
    for half, lit in enumerate((False, True)):
        tile = _disc(cell, lit)
        for y in range(h):
            for x in range(w):
                both.paste(tile, (x * cell, (half * h + y) * cell))
    both = both.quantize(colors=32, dither=0)
    return both.crop((0, 0, w * cell, h * cell)), both.crop((0, h * cell, w * cell, 2 * h * cell))


def render_gif(recorder, minutes=5, speed=10, scale=4):
    """Animated GIF of the last `minutes`, played `speed` times faster, `scale` pixels per dot."""
    from PIL import Image
    frames, start, end = recorder.window(max(0.1, minutes) * 60)
    w, h = recorder.w, recorder.h
    out_len = (end - start) / speed  # seconds of GIF
    step = max(GIF_FRAME_S, out_len / MAX_GIF_FRAMES)
    # sample the recording on the output clock; merge repeats into longer frames
    timeline, i, t = [], 0, 0.0
    while t < out_len or not timeline:
        src = start + t * speed
        while i + 1 < len(frames) and frames[i + 1][0] <= src:
            i += 1
        if timeline and timeline[-1][0] == i:
            timeline[-1][1] += step
        else:
            timeline.append([i, step])
        t += step
    off, on = _boards(w, h, scale)
    images, durations = [], []
    size = (w * scale, h * scale)
    for idx, dur in timeline:
        mask = Image.frombytes("1", (w, h), frames[idx][1]).resize(size, Image.NEAREST)
        im = off.copy()
        im.paste(on, (0, 0), mask)
        images.append(im)
        durations.append(max(20, int(round(dur * 1000 / 10)) * 10))
    durations[-1] = max(durations[-1], 1500)  # hold the last picture before looping
    buf = io.BytesIO()
    images[0].save(buf, "GIF", save_all=True, append_images=images[1:], duration=durations, loop=0)
    return buf.getvalue()
