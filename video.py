"""Videos: uploaded clips converted (with ffmpeg) into a compact board-native format and played by apps/video.py.

On disk, under <data>/videos/<id>/:
    r<N>/seg00000.fdv …   the frames: 8-bit grey, already sized to the board, at a fixed fps
    r<N>/thumbs.bin       a few evenly spaced frames (zlib) for the library grid and the scrub strip
    original/part000 …    the uploaded file in 8 MB parts, only when "keep the original" was on
The library itself (names, status, durations) is videos.json. Everything is a plain file in the data
folder, so storage.py mirrors it into storage.db like the rest; the parts are small enough that the
database never has to hold one huge row.

A segment file (.fdv) holds up to SEG_CHUNKS chunks of CHUNK frames each:
    b"FDV1", u16 width, u16 height, u16 frames per chunk, u16 chunk count, u32 first frame,
    u32 offsets[chunk count + 1] (from the start of the file), then the chunks.
A chunk is zlib(first frame + each following frame as a byte-wise difference from the one before).
Frame i is in segment i // (CHUNK * SEG_CHUNKS), chunk (i // CHUNK) % SEG_CHUNKS: seeking is one read
and one ~50 KB decompress, whatever the length of the video.
"""

import json
import logging
import os
import queue
import re
import shutil
import struct
import subprocess
import threading
import time
import zlib
from pathlib import Path

log = logging.getLogger("video")

MAGIC = b"FDV1"
HEAD = struct.Struct("<4sHHHHI")
CHUNK = 15  # frames per compressed chunk (1 s at 15 fps)
SEG_CHUNKS = 60  # chunks per segment file (a minute at 15 fps; a few hundred KB)
PART = 8 << 20  # kept originals are stored in parts of this size
THUMBS = 24  # frames kept for the scrub strip
# title_source: where the title that scrolls by at the start comes from: "metadata" only, or the file's
# metadata falling back to its name in the library ("name")
DEFAULTS = {"max_upload_mb": 1024, "keep_original": False, "fps": 15, "title_source": "name"}
DISPLAY_DEFAULTS = {"dither": "ordered", "threshold": 128, "contrast": 1.0, "gamma": 1.0, "invert": False}
MODES = ("crop", "fit", "stretch")
NO_FFMPEG = "Videos need ffmpeg, which isn't installed. On the Pi, run: sudo apt install ffmpeg"
UNDO_SECONDS = 30  # a deleted video can be brought back this long


def have_ffmpeg():
    return shutil.which("ffmpeg") is not None


# ---- the chunked file format -------------------------------------------------------------

def _delta(frames):
    out = bytearray(frames[0])
    for a, b in zip(frames, frames[1:]):
        out += bytes([(y - x) & 255 for x, y in zip(a, b)])
    return bytes(out)


def _undelta(data, n):
    frames = [data[:n]]
    for k in range(1, len(data) // n):
        prev = frames[-1]
        frames.append(bytes([(x + y) & 255 for x, y in zip(prev, data[k * n:(k + 1) * n])]))
    return frames


class Writer:
    """Collects frames and writes them out as segment files (each written whole, via a .tmp name)."""

    def __init__(self, folder, w, h):
        self.dir, self.w, self.h = Path(folder), w, h
        self.dir.mkdir(parents=True, exist_ok=True)
        self.n = 0  # frames written
        self.pending = []  # frames of the chunk being built
        self.chunks = []  # compressed chunks of the segment being built
        self.seg_first = 0
        self.bytes = 0

    def add(self, frame):
        self.pending.append(bytes(frame))
        self.n += 1
        if len(self.pending) == CHUNK:
            self._close_chunk()

    def _close_chunk(self):
        if self.pending:
            self.chunks.append(zlib.compress(_delta(self.pending), 6))
            self.pending = []
        if len(self.chunks) == SEG_CHUNKS:
            self._write_segment()

    def _write_segment(self):
        if not self.chunks:
            return
        k = len(self.chunks)
        offsets, pos = [], HEAD.size + 4 * (k + 1)
        for c in self.chunks:
            offsets.append(pos)
            pos += len(c)
        offsets.append(pos)
        f = self.dir / f"seg{self.seg_first // (CHUNK * SEG_CHUNKS):05d}.fdv"
        tmp = f.with_name(f.name + ".tmp")
        with open(tmp, "wb") as out:
            out.write(HEAD.pack(MAGIC, self.w, self.h, CHUNK, k, self.seg_first))
            out.write(struct.pack(f"<{k + 1}I", *offsets))
            for c in self.chunks:
                out.write(c)
        tmp.replace(f)
        self.bytes += pos
        self.chunks = []
        self.seg_first = self.n

    def close(self):
        self._close_chunk()
        self._write_segment()
        return self.n


class Reader:
    """Random access to the frames of one converted video. Keeps the last decoded chunk."""

    def __init__(self, folder, frames, w, h):
        self.dir, self.frames, self.w, self.h = Path(folder), frames, w, h
        self.size = w * h
        self._tables = {}
        self._chunk = (None, None)
        self._lock = threading.Lock()

    def _table(self, seg):
        t = self._tables.get(seg)
        if t is None:
            with open(self.dir / f"seg{seg:05d}.fdv", "rb") as f:
                magic, w, h, per, k, first = HEAD.unpack(f.read(HEAD.size))
                if magic != MAGIC or (w, h) != (self.w, self.h) or per != CHUNK:
                    raise ValueError(f"{self.dir.name}: not a video segment this version can read")
                t = self._tables[seg] = struct.unpack(f"<{k + 1}I", f.read(4 * (k + 1)))
        return t

    def frame(self, i):
        """Grey bytes (w*h) of frame i (clamped to the video)."""
        i = max(0, min(self.frames - 1, int(i)))
        key = i // CHUNK
        with self._lock:
            ck, frames = self._chunk
            if ck != key:
                seg, c = divmod(key, SEG_CHUNKS)
                offs = self._table(seg)
                with open(self.dir / f"seg{seg:05d}.fdv", "rb") as f:
                    f.seek(offs[c])
                    data = zlib.decompress(f.read(offs[c + 1] - offs[c]))
                frames = _undelta(data, self.size)
                self._chunk = (key, frames)
            return frames[min(i % CHUNK, len(frames) - 1)]


def write_thumbs(folder, frames):
    (Path(folder) / "thumbs.bin").write_bytes(zlib.compress(b"".join(frames), 6))


def read_thumbs(folder, size):
    try:
        data = zlib.decompress((Path(folder) / "thumbs.bin").read_bytes())
    except (OSError, zlib.error):
        return []
    return [data[i:i + size] for i in range(0, len(data) - size + 1, size)]


# ---- turning grey frames into dots (the display options, applied at playback) ---------------

BAYER8 = [[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26], [12, 44, 4, 36, 14, 46, 6, 38],
          [60, 28, 52, 20, 62, 30, 54, 22], [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
          [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]]
_maps = {}
_luts = {}


def _bayer_map(w, h):
    m = _maps.get((w, h))
    if m is None:
        m = _maps[(w, h)] = bytes(BAYER8[y % 8][x % 8] * 4 + 2 for y in range(h) for x in range(w))
    return m


def display_opts(p):
    """Clean display options from app params or a query (missing ones get their defaults)."""
    d = {**DISPLAY_DEFAULTS, **{k: v for k, v in (p or {}).items() if k in DISPLAY_DEFAULTS and v not in (None, "")}}
    d["dither"] = d["dither"] if d["dither"] in ("ordered", "diffusion", "threshold") else "ordered"
    d["threshold"] = max(1, min(254, int(float(d["threshold"]))))
    d["contrast"] = max(0.2, min(4.0, float(d["contrast"])))
    d["gamma"] = max(0.2, min(5.0, float(d["gamma"])))
    d["invert"] = d["invert"] in (True, "true", "1", 1, "on")
    return d


def _lut(d):
    """One 256-byte table: contrast, gamma, invert and the brightness cutoff (as a shift for the dithers)."""
    key = (d["dither"], d["threshold"], d["contrast"], d["gamma"], d["invert"])
    t = _luts.get(key)
    if t is None:
        out = []
        for v in range(256):
            x = (v / 255 - 0.5) * d["contrast"] + 0.5
            x = min(1.0, max(0.0, x)) ** d["gamma"]
            if d["invert"]:
                x = 1 - x
            g = x * 255
            if d["dither"] == "threshold":
                out.append(1 if g >= d["threshold"] else 0)
            else:
                out.append(int(min(255, max(0, round(g + 128 - d["threshold"])))))
        t = _luts[key] = bytes(out)
        if len(_luts) > 64:
            _luts.pop(next(iter(_luts)))
    return t


def to_dots(gray, w, h, d):
    """Grey bytes -> bytes of 0/1 per dot, with display options `d` (from display_opts)."""
    g = gray.translate(_lut(d))
    if d["dither"] == "threshold":
        return g
    if d["dither"] == "diffusion":
        from PIL import Image
        return Image.frombytes("L", (w, h), g).convert("1").convert("L").tobytes().translate(_BIT)
    return bytes([a > b for a, b in zip(g, _bayer_map(w, h))])


_BIT = bytes([0] * 128 + [1] * 128)


def dots_rows(dots, w, h):
    s = dots.translate(_ROWCH).decode()
    return [s[y * w:(y + 1) * w] for y in range(h)]


_ROWCH = bytes([48, 49] + [49] * 254)  # 0 -> "0", anything else -> "1"


def scale_gray(gray, w, h, cw, ch):
    """Resize grey frames for a layer of a different size."""
    from PIL import Image
    return Image.frombytes("L", (w, h), gray).resize((cw, ch), Image.BILINEAR).tobytes()


# ---- the library ------------------------------------------------------------------------------

def clean_name(name):
    """A file name made readable as a title: separators to spaces, no "1080p"-style tags."""
    s = re.sub(r"[_.]+", " ", name)
    s = re.sub(r"\b(\d{3,4}p|[hx]26[45]|webm|mp4|mkv|mov|hevc|av1|vp9)\b", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -")


def fmt_time(s):
    s = int(max(0, s))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class Library:
    """videos.json plus the conversion queue (one ffmpeg at a time; the Pi is slow)."""

    def __init__(self, store, width, height, on_purge=None):
        self.store, self.w, self.h = store, width, height
        self.on_purge = on_purge  # called with the id once a deleted video is gone for good
        self.root = Path(store.dir) / "videos"
        self.incoming = self.root / "incoming"
        self.incoming.mkdir(parents=True, exist_ok=True)
        self.videos = store._load("videos.json", {})
        self.lock = threading.RLock()
        self.jobs = queue.Queue()
        self.progress = {}  # id -> 0..1 while converting
        self.proc = None
        self.current = None
        self._readers = {}
        self._posters = {}
        for vid, v in list(self.videos.items()):
            if v.get("deleted"):
                self._purge(vid)
            elif v.get("status") in ("queued", "converting"):
                if self._source(vid):
                    v["status"] = "queued"
                    self.jobs.put(vid)
                else:
                    v.update(status="failed", error="The server restarted while converting this video. Upload it again.")
        self.save()
        threading.Thread(target=self._worker, daemon=True, name="video-convert").start()

    def settings(self):
        return {**DEFAULTS, **(self.store.settings.get("videos") or {})}

    def save(self):
        with self.lock:
            self.store._save("videos.json", self.videos)

    def get(self, vid):
        v = self.videos.get(vid)
        if v is None or v.get("deleted"):
            raise ValueError("That video isn't in the library any more.")
        return v

    def folder(self, vid):
        if not re.fullmatch(r"v[0-9]+", vid or ""):
            raise ValueError("bad video id")
        return self.root / vid

    def frames_dir(self, vid):
        v = self.videos[vid]
        return self.folder(vid) / f"r{v.get('rev', 1)}"

    def reader(self, vid):
        """A Reader for a converted video (cached; replaced after a re-conversion)."""
        v = self.get(vid)
        if v.get("status") != "ready":
            raise ValueError(f"{v['name']} isn't converted yet")
        key = (vid, v.get("rev", 1))
        r = self._readers.get(key)
        if r is None:
            r = self._readers[key] = Reader(self.frames_dir(vid), v["frames"], v["w"], v["h"])
            if len(self._readers) > 8:
                self._readers.pop(next(iter(self._readers)))
        return r

    def public(self, vid):
        v = self.videos[vid]
        out = {"id": vid, **{k: v.get(k) for k in ("name", "status", "error", "fps", "frames", "duration", "mode",
                                                    "bytes", "source_name", "source_bytes", "created", "display",
                                                    "range", "title", "meta_title")}}
        out["has_original"] = bool(v.get("original"))
        if vid in self.progress:
            out["progress"] = round(self.progress[vid], 3)
        if v.get("status") == "ready" and v.get("duration"):
            out["mb_per_hour"] = round(v["bytes"] / v["duration"] * 3600 / 1e6, 1)
        return out

    def listing(self):
        with self.lock:
            items = [self.public(vid) for vid, v in self.videos.items() if not v.get("deleted")]
        for it in items:
            it["poster"] = self.poster(it["id"])
        return items

    def poster(self, vid):
        v = self.videos.get(vid)
        if not v or v.get("status") != "ready":
            return None
        key = (vid, v.get("rev", 1), json.dumps(v.get("display") or {}, sort_keys=True))
        if key not in self._posters:
            thumbs = read_thumbs(self.frames_dir(vid), v["w"] * v["h"])
            if not thumbs:
                return None
            g = thumbs[min(len(thumbs) - 1, len(thumbs) // 5)]
            self._posters[key] = dots_rows(to_dots(g, v["w"], v["h"], display_opts(v.get("display"))), v["w"], v["h"])
        return self._posters[key]

    def strip(self, vid, display):
        """The thumbnail frames as dots: [(seconds, rows)]."""
        v = self.get(vid)
        d = display_opts({**(v.get("display") or {}), **(display or {})})
        thumbs = read_thumbs(self.frames_dir(vid), v["w"] * v["h"])
        return [(round(i / v["fps"], 2), dots_rows(to_dots(g, v["w"], v["h"], d), v["w"], v["h"]))
                for i, g in zip(v.get("thumb_frames") or [], thumbs)]

    def frame_rows(self, vid, t, display):
        """One frame as dots, as it would look on the board (the scrub preview)."""
        v = self.get(vid)
        r = self.reader(vid)
        i = max(0, min(r.frames - 1, int(float(t) * v["fps"])))
        d = display_opts({**(v.get("display") or {}), **(display or {})})
        return round(i / v["fps"], 3), dots_rows(to_dots(r.frame(i), r.w, r.h, d), r.w, r.h)

    def ready_ids(self):
        return [vid for vid, v in self.videos.items() if v.get("status") == "ready" and not v.get("deleted")]

    # ---- uploads ----

    def receive(self, rfile, length, name, mode="crop", fps=None, keep=None):
        """Stream an upload of `length` bytes from `rfile` to disk, then queue it. Returns the new id."""
        if not have_ffmpeg():
            raise ValueError(NO_FFMPEG)
        cfg = self.settings()
        cap = int(float(cfg["max_upload_mb"]) * 1024 * 1024)
        if length <= 0:
            raise ValueError("The upload was empty.")
        if length > cap:
            raise ValueError(f"That file is {length / 1e6:.0f} MB; the limit is {cfg['max_upload_mb']} MB "
                             f"(Videos, Options).")
        free = shutil.disk_usage(self.root).free
        if free < length + (200 << 20):
            raise ValueError(f"Not enough free space for that file ({free / 1e6:.0f} MB free on the server).")
        vid = f"v{int(time.time() * 1000) % 10**12}"
        tmp = self.incoming / f"{vid}.upload.tmp"
        got = 0
        try:
            with open(tmp, "wb") as f:
                while got < length:
                    block = rfile.read(min(1 << 20, length - got))
                    if not block:
                        break
                    f.write(block)
                    got += len(block)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        if got < length:
            tmp.unlink(missing_ok=True)
            raise ValueError("The upload was cut off. Try again.")
        stem = Path(name).stem.strip() or "Video"
        keep = cfg["keep_original"] if keep is None else keep
        with self.lock:
            self.videos[vid] = {"name": stem[:80], "status": "queued", "mode": mode if mode in MODES else "crop",
                                "fps": max(1, min(30, int(float(fps or cfg["fps"])))),
                                "source_name": Path(name).name[:120], "source_bytes": got, "created": time.time(),
                                "keep": bool(keep), "rev": 0}
            self.save()
        self.jobs.put(vid)
        return vid

    def _source(self, vid):
        """What ffmpeg should read for this video: the upload waiting in incoming/, or the kept original."""
        tmp = self.incoming / f"{vid}.upload.tmp"
        if tmp.exists():
            return str(tmp)
        parts = sorted((self.folder(vid) / "original").glob("part*"))
        if parts:
            return str(parts[0]) if len(parts) == 1 else "concat:" + "|".join(str(p) for p in parts)
        return None

    def reconvert(self, vid, mode=None, fps=None):
        v = self.get(vid)
        if not v.get("original"):
            raise ValueError("The original file wasn't kept, so this video can't be converted again. Upload it again.")
        if v.get("status") in ("queued", "converting"):
            raise ValueError("It's already being converted.")
        with self.lock:
            if mode in MODES:
                v["mode"] = mode
            if fps:
                v["fps"] = max(1, min(30, int(float(fps))))
            v["status"], v["error"] = "queued", None
            self.save()
        self.jobs.put(vid)

    # ---- conversion ----

    def _worker(self):
        while True:
            vid = self.jobs.get()
            v = self.videos.get(vid)
            if not v or v.get("deleted") or v.get("status") != "queued":
                continue
            try:
                self._convert(vid)
            except Exception as e:
                log.warning("converting %s failed: %s", vid, e)
                with self.lock:
                    if vid in self.videos:
                        self.videos[vid].update(status="failed" if not self.videos[vid].get("frames") else "ready",
                                                error=str(e))
                        self.save()
            finally:
                self.progress.pop(vid, None)
                self.current = self.proc = None

    def _probe(self, src):
        """(duration in seconds or None, title from the file's metadata or None)."""
        if not shutil.which("ffprobe"):
            return None, None
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags:stream_tags",
                                  "-of", "json", src], capture_output=True, text=True, timeout=60).stdout
            info = json.loads(out or "{}")
        except (ValueError, subprocess.SubprocessError, OSError):
            return None, None
        try:
            duration = float(info.get("format", {}).get("duration"))
        except (TypeError, ValueError):
            duration = None
        title = None
        for tags in [info.get("format", {}).get("tags") or {}] + [s.get("tags") or {} for s in info.get("streams") or []]:
            title = next((str(v).strip() for k, v in tags.items() if k.lower() == "title" and str(v).strip()), None)
            if title:
                break
        return duration, title

    def _filter(self, mode, fps):
        w, h = self.w, self.h
        size = {"stretch": f"scale={w}:{h}:flags=area",
                "fit": f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=area,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
                "crop": f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=area,crop={w}:{h}"}[mode]
        return f"fps={fps},scale=iw*sar:ih,setsar=1,{size},format=gray"

    def _convert(self, vid):
        if not have_ffmpeg():
            raise ValueError(NO_FFMPEG)
        v = self.videos[vid]
        src = self._source(vid)
        if src is None:
            raise ValueError("The uploaded file is missing. Upload it again.")
        with self.lock:
            v["status"], v["error"] = "converting", None
            self.save()
        self.current = vid
        self.progress[vid] = 0.0
        duration, meta_title = self._probe(src)
        fps, rev = v["fps"], int(v.get("rev") or 0) + 1
        out_dir = self.folder(vid) / f"r{rev}"
        shutil.rmtree(out_dir, ignore_errors=True)
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:2",
               "-i", src, "-an", "-sn", "-dn", "-vf", self._filter(v["mode"], fps),
               "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
        self.proc = proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            preexec_fn=lambda: os.nice(10))  # playback comes first
        errors = []

        def read_progress():
            for line in proc.stderr:
                line = line.decode(errors="replace").strip()
                key, eq, val = line.partition("=")
                if eq and key in ("out_time_us", "out_time_ms") and val.isdigit() and duration:
                    self.progress[vid] = min(0.99, int(val) / 1e6 / duration)
                elif eq and re.fullmatch(r"[a-z_0-9]+", key):
                    pass  # other progress fields
                elif line:
                    errors.append(line)
        t = threading.Thread(target=read_progress, daemon=True)
        t.start()
        w, h = self.w, self.h
        n = w * h
        writer = Writer(out_dir, w, h)
        stride = max(1, int(round((duration or 0) * fps / THUMBS)) or 1) if duration else None
        thumbs = []
        while True:
            frame = proc.stdout.read(n)
            if len(frame) < n:
                break
            if writer.n % (stride or 15) == 0:
                thumbs.append((writer.n, frame))
            writer.add(frame)
            if self.videos.get(vid, {}).get("deleted"):
                proc.kill()
                break
        total = writer.close()
        proc.wait()
        t.join(timeout=5)
        if self.videos.get(vid, {}).get("deleted") or vid not in self.videos:
            shutil.rmtree(out_dir, ignore_errors=True)
            return
        if total == 0:
            shutil.rmtree(out_dir, ignore_errors=True)
            msg = errors[-1] if errors else "ffmpeg found no video in that file"
            raise ValueError(f"Couldn't convert {v['source_name']}: {msg}")
        if len(thumbs) > THUMBS:  # no duration up front: thin out evenly
            thumbs = [thumbs[int(i * len(thumbs) / THUMBS)] for i in range(THUMBS)]
        write_thumbs(out_dir, [f for _, f in thumbs])
        # keep the original (in parts) if asked, then drop the upload
        tmp = self.incoming / f"{vid}.upload.tmp"
        if tmp.exists():
            if v.get("keep"):
                self._split_original(vid, tmp)
            tmp.unlink(missing_ok=True)
        old = [d for d in self.folder(vid).glob("r*") if d.is_dir() and d.name != f"r{rev}"]
        with self.lock:
            v["meta_title"] = meta_title
            if not v.get("title_set"):
                v["title"] = meta_title or (clean_name(v["name"]) if self.settings()["title_source"] == "name" else "")
            v.update(status="ready", error=None, rev=rev, frames=total, duration=round(total / fps, 3), w=w, h=h,
                     bytes=writer.bytes, thumb_frames=[i for i, _ in thumbs],
                     original=bool(list((self.folder(vid) / "original").glob("part*"))))
            self.save()
        for d in old:
            shutil.rmtree(d, ignore_errors=True)
        self.store.sync(f"videos/{vid}")
        log.info("converted %s (%s): %d frames, %.1f MB", vid, v["name"], total, writer.bytes / 1e6)

    def _split_original(self, vid, src):
        d = self.folder(vid) / "original"
        d.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as f:
            i = 0
            while True:
                block = f.read(PART)
                if not block:
                    break
                p = d / f"part{i:03d}"
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_bytes(block)
                tmp.replace(p)
                i += 1

    # ---- rename / delete with undo ----

    def update(self, vid, name=None, display=None, rng=None, title=None):
        v = self.get(vid)
        with self.lock:
            if title is not None:  # "" clears it: nothing scrolls by
                v["title"], v["title_set"] = str(title).strip()[:120], True
            if name is not None:
                name = str(name).strip()
                if not name:
                    raise ValueError("The name is empty.")
                v["name"] = name[:80]
            if display is not None:
                v["display"] = display_opts(display)
            if rng is not None:
                v["range"] = rng
            self.save()
        return self.public(vid)

    def delete(self, vid):
        v = self.get(vid)
        with self.lock:
            v["deleted"] = time.time()
            self.save()
        if self.current == vid and self.proc:
            self.proc.kill()
        threading.Timer(UNDO_SECONDS, self._purge_if_deleted, args=(vid, v["deleted"])).start()

    def restore(self, vid):
        v = self.videos.get(vid)
        if v is None or not v.get("deleted"):
            raise ValueError("That video can't be brought back any more.")
        with self.lock:
            v.pop("deleted", None)
            if v.get("status") in ("queued", "converting"):
                v["status"] = "failed"
                v["error"] = "Conversion was cancelled when it was deleted." + (
                    " Convert it again." if v.get("original") else " Upload it again.")
            self.save()
        return self.public(vid)

    def _purge_if_deleted(self, vid, stamp):
        v = self.videos.get(vid)
        if v and v.get("deleted") == stamp:
            self._purge(vid)

    def _purge(self, vid):
        with self.lock:
            self.videos.pop(vid, None)
            self.save()
        shutil.rmtree(self.folder(vid), ignore_errors=True)
        (self.incoming / f"{vid}.upload.tmp").unlink(missing_ok=True)
        self.store.sync(f"videos/{vid}")
        self._readers = {k: r for k, r in self._readers.items() if k[0] != vid}
        if self.on_purge:
            try:
                self.on_purge(vid)
            except Exception:
                log.exception("cleaning up after video %s failed", vid)


library: Library = None  # set by server.py; apps/video.py reads it
