"""QR code encoder (byte mode, versions 1-40, error correction L/M/Q/H), pure Python.

    code = qr.encode("WIFI:T:WPA;S:Home;P:secret;;", ecc="M", max_version=5)
    code.size, code.version, code.modules[y][x]   # True = dark module
    qr.draw(canvas, code, x, y)                   # lit background, dark modules

Follows ISO/IEC 18004: Reed-Solomon over GF(256), block interleaving, all 8 masks scored with
the standard penalty rules, BCH-coded format and version information.
"""

ECC_LEVELS = {"L": 1, "M": 0, "Q": 3, "H": 2}  # value = format-info bits
_ECC_ORDER = "LMQH"
# per version 1..40 (index 0 unused), for L, M, Q, H
_ECC_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
)
_NUM_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
)

# GF(256) with the QR polynomial x^8 + x^4 + x^3 + x^2 + 1
_EXP = [0] * 512
_LOG = [0] * 256
_v = 1
for _i in range(255):
    _EXP[_i] = _v
    _LOG[_v] = _i
    _v <<= 1
    if _v & 0x100:
        _v ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_divisor(degree):
    out = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            out[j] = _mul(out[j], root)
            if j + 1 < degree:
                out[j] ^= out[j + 1]
        root = _mul(root, 2)
    return out


def _rs_remainder(data, divisor):
    out = [0] * len(divisor)
    for b in data:
        factor = b ^ out.pop(0)
        out.append(0)
        if factor:
            for i, coef in enumerate(divisor):
                out[i] ^= _mul(coef, factor)
    return out


def _raw_modules(ver):
    """Data + ECC modules available in a version (everything except function patterns)."""
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        na = ver // 7 + 2
        n -= (25 * na - 10) * na - 55
        if ver >= 7:
            n -= 36
    return n


def capacity(ver, ecc):
    """Data codewords (bytes) in this version / ECC level."""
    e = _ECC_ORDER.index(ecc)
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[e][ver] * _NUM_BLOCKS[e][ver]


def max_bytes(ver, ecc):
    """How many bytes of byte-mode data fit."""
    return (capacity(ver, ecc) * 8 - 4 - (8 if ver < 10 else 16)) // 8


def _alignment_positions(ver):
    if ver == 1:
        return []
    size = ver * 4 + 17
    na = ver // 7 + 2
    step = (ver * 8 + na * 3 + 5) // (na * 4 - 4) * 2
    return [6] + [size - 7 - i * step for i in reversed(range(na - 1))]


class QrCode:
    def __init__(self, version, ecc, modules, mask):
        self.version, self.ecc, self.modules, self.mask = version, ecc, modules, mask
        self.size = len(modules)

    def rows(self, quiet=0):
        """'1' = dark module, as strings, with `quiet` light modules around."""
        blank = "0" * (self.size + 2 * quiet)
        body = ["0" * quiet + "".join("1" if v else "0" for v in r) + "0" * quiet for r in self.modules]
        return [blank] * quiet + body + [blank] * quiet


def encode(data, ecc="M", min_version=1, max_version=40, boost=True):
    """Encode str/bytes in byte mode with the smallest version that fits. With boost, the ECC level is
    raised (M -> Q -> H) when that doesn't need a bigger version. Raises ValueError if it doesn't fit."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    ecc = ecc.upper()
    if ecc not in ECC_LEVELS:
        raise ValueError("ecc must be L, M, Q or H")
    for ver in range(max(1, min_version), min(40, max_version) + 1):
        if len(data) <= max_bytes(ver, ecc):
            break
    else:
        raise ValueError(f"too much data for a QR code up to version {max_version} ({len(data)} bytes)")
    if boost:
        for better in _ECC_ORDER[_ECC_ORDER.index(ecc) + 1:]:
            if len(data) <= max_bytes(ver, better):
                ecc = better
    return _build(data, ver, ecc)


def _build(data, ver, ecc):
    # bit stream: mode 0100 (byte), character count, data, terminator, padding
    bits = []
    put = lambda val, n: bits.extend((val >> i) & 1 for i in reversed(range(n)))
    put(4, 4)
    put(len(data), 8 if ver < 10 else 16)
    for b in data:
        put(b, 8)
    cap_bits = capacity(ver, ecc) * 8
    put(0, min(4, cap_bits - len(bits)))
    put(0, -len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < cap_bits // 8:
        words.append(pad)
        pad ^= 0xEC ^ 0x11

    # split into blocks, add Reed-Solomon codewords, interleave
    e = _ECC_ORDER.index(ecc)
    nblocks, ecc_len = _NUM_BLOCKS[e][ver], _ECC_PER_BLOCK[e][ver]
    raw = _raw_modules(ver) // 8
    nshort = nblocks - raw % nblocks
    short_len = raw // nblocks
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(nblocks):
        dlen = short_len - ecc_len + (0 if i < nshort else 1)
        d = words[k:k + dlen]
        k += dlen
        blocks.append((d, _rs_remainder(d, divisor)))
    codewords = []
    for i in range(short_len - ecc_len + 1):
        for d, _ in blocks:
            if i < len(d):
                codewords.append(d[i])
    for i in range(ecc_len):
        for _, r in blocks:
            codewords.append(r[i])

    size = ver * 4 + 17
    mods = [[False] * size for _ in range(size)]
    func = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        mods[y][x] = dark
        func[y][x] = True

    for i in range(size):  # timing patterns
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):  # finders + separators
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    al = _alignment_positions(ver)
    for i, ay in enumerate(al):
        for j, ax in enumerate(al):
            if (i == 0 and j == 0) or (i == 0 and j == len(al) - 1) or (i == len(al) - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
    _draw_format(setf, size, ecc, 0)  # reserve the format areas
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        vbits = ver << 12 | rem
        for i in range(18):
            bit = (vbits >> i) & 1 == 1
            a, b = size - 11 + i % 3, i // 3
            setf(a, b, bit)
            setf(b, a, bit)

    # data in the zigzag order: two-column strips from the right, alternating up and down
    i, total = 0, len(codewords) * 8
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = (right + 1) & 2 == 0
        for vert in range(size):
            y = size - 1 - vert if upward else vert
            for x in (right, right - 1):
                if not func[y][x] and i < total:
                    mods[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2

    best = None
    for mask in range(8):
        m = _apply_mask(mods, func, mask)
        _draw_format(lambda x, y, d: m[y].__setitem__(x, d), size, ecc, mask)
        score = _penalty(m)
        if best is None or score < best[0]:
            best = (score, mask, m)
    return QrCode(ver, ecc, best[2], best[1])


_MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def _apply_mask(mods, func, mask):
    f = _MASKS[mask]
    n = len(mods)
    return [[mods[y][x] ^ (not func[y][x] and f(x, y)) for x in range(n)] for y in range(n)]


def _draw_format(setf, size, ecc, mask):
    data = ECC_LEVELS[ecc] << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = (data << 10 | rem) ^ 0x5412
    bit = lambda i: (bits >> i) & 1 == 1
    for i in range(6):
        setf(8, i, bit(i))
    setf(8, 7, bit(6))
    setf(8, 8, bit(7))
    setf(7, 8, bit(8))
    for i in range(9, 15):
        setf(14 - i, 8, bit(i))
    for i in range(8):
        setf(size - 1 - i, 8, bit(i))
    for i in range(8, 15):
        setf(8, size - 15 + i, bit(i))
    setf(8, size - 8, True)  # the always-dark module


def _penalty(m):
    n = len(m)
    score = 0
    finder_a = [True, False, True, True, True, False, True, False, False, False, False]
    finder_b = finder_a[::-1]
    for lines in (m, [list(col) for col in zip(*m)]):
        for line in lines:
            run, prev = 0, None
            for v in line:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run, prev = 1, v
            if run >= 5:
                score += run - 2
            for i in range(n - 10):
                seg = line[i:i + 11]
                if seg == finder_a or seg == finder_b:
                    score += 40
    for y in range(n - 1):
        r0, r1 = m[y], m[y + 1]
        for x in range(n - 1):
            if r0[x] == r0[x + 1] == r1[x] == r1[x + 1]:
                score += 3
    dark = sum(sum(r) for r in m)
    score += abs(dark * 20 - n * n * 10) // (n * n) * 10
    return score


def draw(c, code, x, y, scale=1, quiet=0, invert=False):
    """Draw onto a Canvas: background (incl. `quiet` modules around it) lit, dark modules off.
    invert=True draws lit modules on black (most phone scanners read that too)."""
    full = (code.size + 2 * quiet) * scale
    ink = 1 if invert else 0
    c.rect(x, y, full, full, v=1 - ink, fill=True)
    for my, row in enumerate(code.modules):
        for mx, is_dark in enumerate(row):
            if is_dark:
                px, py = x + (mx + quiet) * scale, y + (my + quiet) * scale
                for dy in range(scale):
                    for dx in range(scale):
                        c.set(px + dx, py + dy, ink)
    return full
