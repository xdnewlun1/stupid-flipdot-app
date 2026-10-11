"""The few per-dot algorithms that can't be written with slices, bytes.translate, big integers or Pillow: the
morph's distance fields and blend, and a Game of Life generation. Each exists twice, in C (native/fast/fast.c, loaded
through ctypes) and in Python here, with the same results (tests/test_fast.py). The C version is built with the
system compiler the first time it's needed (under a second on a Pi 5); without a compiler the Python one runs,
which is what the board did before, a few ms per picture instead of a few tens of µs.

FLIPDOT_NO_NATIVE=1 in the environment keeps the Python versions (for comparing the two).
"""

import ctypes
import logging
import os
import shutil
import subprocess
from array import array
from pathlib import Path

log = logging.getLogger("fastpath")

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "native" / "fast" / "fast.c"
BUILD = ROOT / "native" / "fast" / "build"
LIB = BUILD / "libfast.so"


def _build():
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise OSError("no C compiler (sudo apt install build-essential)")
    BUILD.mkdir(parents=True, exist_ok=True)
    tmp = LIB.with_suffix(".so.tmp")
    subprocess.run([cc, "-O2", "-shared", "-fPIC", "-o", str(tmp), str(SRC)], check=True, capture_output=True,
                   text=True, timeout=120)
    os.replace(tmp, LIB)


def _load():
    if os.environ.get("FLIPDOT_NO_NATIVE"):
        return None
    try:
        if not LIB.exists() or LIB.stat().st_mtime < SRC.stat().st_mtime:
            _build()
        try:
            lib = ctypes.CDLL(str(LIB))
        except OSError:  # a build copied from another machine (e.g. an x86 PC to the Pi): build it here
            _build()
            lib = ctypes.CDLL(str(LIB))
    except Exception as e:  # noqa: BLE001 - any failure here just means the Python versions run
        detail = e.stderr.strip() if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e)
        log.info("native helpers not available (%s); using the pure-Python versions", detail)
        return None
    u8, i32 = ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_int32)
    lib.fast_distance_field.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.fast_morph_blend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_double, ctypes.c_void_p]
    lib.fast_life_step.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    for f in (lib.fast_distance_field, lib.fast_morph_blend, lib.fast_life_step):
        f.restype = None
    del u8, i32
    return lib


_lib = _load()
available = _lib is not None


def _addr(buf):
    """The address of a writable buffer (bytearray or array)."""
    return ctypes.addressof(ctypes.c_char.from_buffer(buf))


# ---- distance field (transitions morph, visualizer Mix morph) --------------------------------------------------

def _distance_field_py(px, w, h):
    INF = 10**6
    W2 = w + 2
    inner = [(y + 1) * W2 + 1 for y in range(h)]  # where each picture row starts in the padded grid

    def dist(target):
        d = [INF] * (W2 * (h + 2))
        for y, s in enumerate(inner):
            d[s:s + w] = [0 if v == target else INF for v in px[y * w:(y + 1) * w]]
        for s in inner:  # forward pass
            for i in range(s, s + w):
                b = d[i]
                if b:
                    t = d[i - 1] + 3
                    if t < b: b = t
                    t = d[i - W2] + 3
                    if t < b: b = t
                    t = d[i - W2 - 1] + 4
                    if t < b: b = t
                    t = d[i - W2 + 1] + 4
                    if t < b: b = t
                    d[i] = b
        for s in reversed(inner):  # backward pass
            for i in range(s + w - 1, s - 1, -1):
                b = d[i]
                if b:
                    t = d[i + 1] + 3
                    if t < b: b = t
                    t = d[i + W2] + 3
                    if t < b: b = t
                    t = d[i + W2 + 1] + 4
                    if t < b: b = t
                    t = d[i + W2 - 1] + 4
                    if t < b: b = t
                    d[i] = b
        return [v for s in inner for v in d[s:s + w]]

    to_black, to_white = dist(0), dist(1)
    return array("i", [to_black[i] if v else -to_white[i] for i, v in enumerate(px)])


def distance_field(px, w, h):
    """Signed chamfer (3-4) distance to the white/black boundary for a picture (bytes of 0/1): positive inside white
    areas, negative inside black ones, as an array('i') in chamfer units (3 per step)."""
    if _lib is None:
        return _distance_field_py(px, w, h)
    out = array("i", bytes(4 * w * h))
    _lib.fast_distance_field(bytes(px), w, h, _addr(out))
    return out


def _morph_blend_py(a, b, p):
    q = 1 - p
    return bytes(1 if q * da + p * db > 0 else 0 for da, db in zip(a, b))


def morph_blend(a, b, p):
    """The morph picture at p (0..1) between two distance fields: white where the blend is positive."""
    if _lib is None or not isinstance(a, array) or not isinstance(b, array):
        return _morph_blend_py(a, b, p)
    n = len(a)
    out = bytearray(n)
    _lib.fast_morph_blend(_addr(a), _addr(b), n, float(p), _addr(out))
    return bytes(out)


# ---- Game of Life ------------------------------------------------------------------------------------------

def _life_step_py(cells, w, h):
    nxt = bytearray(w * h)
    for y in range(h):
        up, dn, row = ((y - 1) % h) * w, ((y + 1) % h) * w, y * w
        for x in range(w):
            l, r = (x - 1) % w, (x + 1) % w
            n = (cells[up + l] + cells[up + x] + cells[up + r] + cells[row + l] + cells[row + r]
                 + cells[dn + l] + cells[dn + x] + cells[dn + r])
            nxt[row + x] = 1 if n == 3 or (n == 2 and cells[row + x]) else 0
    return nxt


def life_step(cells, w, h):
    """One generation of Conway's Life on a torus. cells: bytes of 0/1; returns a new bytearray."""
    if _lib is None:
        return _life_step_py(cells, w, h)
    out = bytearray(w * h)
    _lib.fast_life_step(bytes(cells), w, h, _addr(out))
    return out
