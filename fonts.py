"""Bitmap fonts. Glyphs are written as rows of 0/1; empty side columns are trimmed
automatically to make the fonts proportional (except in monospace mode)."""

_NORMAL = {  # 5x7, modelled on the classic HD44780 character LCD font
    " ": "00000 00000 00000 00000 00000 00000 00000",
    "!": "00100 00100 00100 00100 00000 00000 00100",
    '"': "01010 01010 01010 00000 00000 00000 00000",
    "#": "01010 01010 11111 01010 11111 01010 01010",
    "$": "00100 01111 10100 01110 00101 11110 00100",
    "%": "11000 11001 00010 00100 01000 10011 00011",
    "&": "01100 10010 10100 01000 10101 10010 01101",
    "'": "00100 00100 01000 00000 00000 00000 00000",
    "(": "00010 00100 01000 01000 01000 00100 00010",
    ")": "01000 00100 00010 00010 00010 00100 01000",
    "*": "00000 00100 10101 01110 10101 00100 00000",
    "+": "00000 00100 00100 11111 00100 00100 00000",
    ",": "00000 00000 00000 00000 01100 00100 01000",
    "-": "00000 00000 00000 11111 00000 00000 00000",
    ".": "00000 00000 00000 00000 00000 01100 01100",
    "/": "00000 00001 00010 00100 01000 10000 00000",
    "0": "01110 10001 10011 10101 11001 10001 01110",
    "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111",
    "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010",
    "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110",
    "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110",
    "9": "01110 10001 10001 01111 00001 00010 01100",
    ":": "00000 01100 01100 00000 01100 01100 00000",
    ";": "00000 01100 01100 00000 01100 00100 01000",
    "<": "00010 00100 01000 10000 01000 00100 00010",
    "=": "00000 00000 11111 00000 11111 00000 00000",
    ">": "01000 00100 00010 00001 00010 00100 01000",
    "?": "01110 10001 00001 00010 00100 00000 00100",
    "@": "01110 10001 00001 01101 10101 10101 01110",
    "A": "01110 10001 10001 10001 11111 10001 10001",
    "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110",
    "D": "11100 10010 10001 10001 10001 10010 11100",
    "E": "11111 10000 10000 11110 10000 10000 11111",
    "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111",
    "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110",
    "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001",
    "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001",
    "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110",
    "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101",
    "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110",
    "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110",
    "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010",
    "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 10001 01010 00100 00100 00100",
    "Z": "11111 00001 00010 00100 01000 10000 11111",
    "[": "01110 01000 01000 01000 01000 01000 01110",
    "\\": "00000 10000 01000 00100 00010 00001 00000",
    "]": "01110 00010 00010 00010 00010 00010 01110",
    "^": "00100 01010 10001 00000 00000 00000 00000",
    "_": "00000 00000 00000 00000 00000 00000 11111",
    "`": "01000 00100 00010 00000 00000 00000 00000",
    "a": "00000 00000 01110 00001 01111 10001 01111",
    "b": "10000 10000 10110 11001 10001 10001 11110",
    "c": "00000 00000 01110 10000 10000 10001 01110",
    "d": "00001 00001 01101 10011 10001 10001 01111",
    "e": "00000 00000 01110 10001 11111 10000 01110",
    "f": "00110 01001 01000 11100 01000 01000 01000",
    "g": "00000 01111 10001 10001 01111 00001 01110",
    "h": "10000 10000 10110 11001 10001 10001 10001",
    "i": "00100 00000 01100 00100 00100 00100 01110",
    "j": "00010 00000 00110 00010 00010 10010 01100",
    "k": "10000 10000 10010 10100 11000 10100 10010",
    "l": "01100 00100 00100 00100 00100 00100 01110",
    "m": "00000 00000 11010 10101 10101 10001 10001",
    "n": "00000 00000 10110 11001 10001 10001 10001",
    "o": "00000 00000 01110 10001 10001 10001 01110",
    "p": "00000 00000 11110 10001 11110 10000 10000",
    "q": "00000 00000 01101 10011 01111 00001 00001",
    "r": "00000 00000 10110 11001 10000 10000 10000",
    "s": "00000 00000 01110 10000 01110 00001 11110",
    "t": "01000 01000 11100 01000 01000 01001 00110",
    "u": "00000 00000 10001 10001 10001 10011 01101",
    "v": "00000 00000 10001 10001 10001 01010 00100",
    "w": "00000 00000 10001 10001 10101 10101 01010",
    "x": "00000 00000 10001 01010 00100 01010 10001",
    "y": "00000 00000 10001 10001 01111 00001 01110",
    "z": "00000 00000 11111 00010 00100 01000 11111",
    "{": "00010 00100 00100 01000 00100 00100 00010",
    "|": "00100 00100 00100 00100 00100 00100 00100",
    "}": "01000 00100 00100 00010 00100 00100 01000",
    "~": "00000 00000 01000 10101 00010 00000 00000",
    "°": "01100 10010 10010 01100 00000 00000 00000",
}

_SMALL = {  # 3x5; lowercase falls back to uppercase
    " ": "00 00 00 00 00",
    "A": "010 101 111 101 101", "B": "110 101 110 101 110", "C": "011 100 100 100 011",
    "D": "110 101 101 101 110", "E": "111 100 110 100 111", "F": "111 100 110 100 100",
    "G": "011 100 101 101 011", "H": "101 101 111 101 101", "I": "111 010 010 010 111",
    "J": "001 001 001 101 010", "K": "101 101 110 101 101", "L": "100 100 100 100 111",
    "M": "101 111 111 101 101", "N": "110 101 101 101 101", "O": "010 101 101 101 010",
    "P": "110 101 110 100 100", "Q": "010 101 101 110 011", "R": "110 101 110 101 101",
    "S": "011 100 010 001 110", "T": "111 010 010 010 010", "U": "101 101 101 101 111",
    "V": "101 101 101 101 010", "W": "101 101 111 111 101", "X": "101 101 010 101 101",
    "Y": "101 101 010 010 010", "Z": "111 001 010 100 111",
    "0": "111 101 101 101 111", "1": "010 110 010 010 111", "2": "110 001 010 100 111",
    "3": "110 001 010 001 110", "4": "101 101 111 001 001", "5": "111 100 110 001 110",
    "6": "011 100 111 101 111", "7": "111 001 010 010 010", "8": "111 101 111 101 111",
    "9": "111 101 111 001 110",
    ".": "0 0 0 0 1", ",": "00 00 00 01 10", ":": "0 1 0 1 0", ";": "00 01 00 01 10",
    "!": "1 1 1 0 1", "?": "110 001 010 000 010", "-": "000 000 111 000 000",
    "+": "000 010 111 010 000", "/": "001 001 010 100 100", "%": "101 001 010 100 101",
    "°": "010 101 010 000 000", "'": "1 1 0 0 0", '"': "101 101 000 000 000",
    "(": "01 10 10 10 01", ")": "10 01 01 01 10", "$": "011 110 010 011 110",
    "#": "101 111 101 111 101", "&": "010 101 010 101 011", "*": "101 010 101 000 000",
    "=": "000 111 000 111 000", "<": "001 010 100 010 001", ">": "100 010 001 010 100",
    "_": "000 000 000 000 111", "@": "111 101 111 100 011", "[": "11 10 10 10 11",
    "]": "11 01 01 01 11", "|": "1 1 1 1 1",
}


class Font:
    spacing = 1  # dots between characters

    def __init__(self, data, height, monospace=False, fallback_upper=False):
        self.height = height
        self.fallback_upper = fallback_upper
        self.glyphs = {}
        for ch, spec in data.items():
            rows = spec.split()
            assert len(rows) == height, ch
            if not monospace and ch != " ":
                cols = [x for x in range(len(rows[0])) if any(r[x] == "1" for r in rows)]
                if cols:
                    rows = [r[cols[0]:cols[-1] + 1] for r in rows]
            self.glyphs[ch] = rows

    def glyph(self, ch):
        g = self.glyphs.get(ch)
        if g is None and self.fallback_upper:
            g = self.glyphs.get(ch.upper())
        return g if g is not None else self.glyphs.get("?")

    def width(self, text, spacing=None):
        if not text:
            return 0
        spacing = self.spacing if spacing is None else spacing
        return sum(len(self.glyph(c)[0]) for c in text) + spacing * (len(text) - 1)


def derive(base, transform, height):
    """A new pixel font made by transforming every glyph of `base` (rows -> rows)."""
    f = Font.__new__(Font)
    f.height, f.fallback_upper = height, base.fallback_upper
    f.glyphs = {ch: transform(rows) for ch, rows in base.glyphs.items()}
    return f


def _bold(rows):
    # smear each dot one column to the right
    return ["".join("1" if (r + "0")[i] == "1" or ("0" + r)[i] == "1" else "0" for i in range(len(r) + 1)) for r in rows]


def _tall(rows):
    return [r for r in rows for _ in (0, 1)]


def _wide(rows):
    return ["".join(c * 2 for c in r) for r in rows]


NORMAL = Font(_NORMAL, 7)
MONO = Font(_NORMAL, 7, monospace=True)
SMALL = Font(_SMALL, 5, fallback_upper=True)
BOLD = derive(NORMAL, _bold, 7)

# Built-in pixel fonts: id -> (label, font)
PIXEL_FONTS = {
    "small": ("Pixel small (3x5)", SMALL),
    "normal": ("Pixel (5x7)", NORMAL),
    "bold": ("Pixel bold (6x7)", BOLD),
    "mono": ("Pixel mono (5x7, fixed width)", MONO),
    "tall": ("Pixel tall (5x14)", derive(NORMAL, _tall, 14)),
    "bold-tall": ("Pixel bold tall (6x14)", derive(BOLD, _tall, 14)),
    "wide": ("Pixel wide (10x7)", derive(NORMAL, _wide, 7)),
    "small-bold": ("Pixel small bold (4x5)", derive(SMALL, _bold, 5)),
}
FONTS = {k: f for k, (_, f) in PIXEL_FONTS.items()}


# ---- outline fonts (system TrueType/OpenType, or uploaded) ---------------------------

class OutlineFont:
    """A TrueType/OpenType (or BDF/PCF bitmap) font rendered at a pixel size with no anti-aliasing."""
    spacing = 0  # the font's own advance widths already include spacing
    fallback_upper = False

    def __init__(self, path, size):
        from PIL import Image, ImageDraw, ImageFont
        self._Image, self._ImageDraw = Image, ImageDraw
        if str(path).endswith(".pil"):
            self.f = ImageFont.load(str(path))
            self.ascent, self.descent = self.f.getbbox("Ag|")[3], 0
        else:
            self.f = ImageFont.truetype(str(path), size)
            self.ascent, self.descent = self.f.getmetrics()
        self.glyphs = {}
        # crop away rows that no ASCII glyph uses, so text sits tightly
        top, bottom = 10**6, 0
        for ch in (chr(c) for c in range(33, 127)):
            box = self._render(ch).getbbox()
            if box:
                top, bottom = min(top, box[1]), max(bottom, box[3])
        self.top = 0 if bottom == 0 else top
        self.height = max(1, bottom - self.top)

    def _render(self, ch):
        w = max(1, int(round(self.f.getlength(ch))))
        img = self._Image.new("1", (w, self.ascent + self.descent))
        d = self._ImageDraw.Draw(img)
        d.fontmode = "1"  # crisp, hinted, no anti-aliasing
        d.text((0, 0), ch, font=self.f, fill=1)
        return img

    def glyph(self, ch):
        g = self.glyphs.get(ch)
        if g is None:
            img = self._render(ch)
            px = img.load()
            g = ["".join("1" if px[x, y] else "0" for x in range(img.width))
                 for y in range(self.top, self.top + self.height)]
            if not g or not g[0]:
                g = ["0"] * self.height
            self.glyphs[ch] = g
        return g

    def width(self, text, spacing=None):
        return sum(len(self.glyph(c)[0]) for c in text)


# ---- font catalogue -------------------------------------------------------------------

import logging as _logging
import re as _re
import subprocess as _subprocess
from pathlib import Path as _Path

_log = _logging.getLogger("fonts")
USER_FONT_DIR = None  # set by the server to data/fonts
_SKIP = _re.compile(r"CJK|Emoji|Arabic|Naskh|Kufi|Armenian|Devanagari|Ethiopic|Georgian|Hebrew|Japanese|Tamil|"
                    r"Thai|Fallback|NKo|Symbol|Jomolhari|Madan|Padauk|PakType|RIT |Vazirmatn", _re.I)
_system = None
_cache = {}


def system_fonts():
    """{id: (label, path)} for installed Regular/Bold fonts (via fontconfig)."""
    global _system
    if _system is None:
        _system = {}
        try:
            out = _subprocess.run(["fc-list", "--format", "%{family[0]}|%{style[0]}|%{file}\n"],
                                  capture_output=True, text=True, timeout=10).stdout
        except (OSError, _subprocess.SubprocessError):
            out = ""
        for line in sorted(set(out.splitlines())):
            family, style, path = (line.split("|") + ["", ""])[:3]
            if not _re.search(r"\.(ttf|otf|ttc)$", path, _re.I) or _SKIP.search(family):
                continue
            # Noto ships a family per writing system; keep just the Latin ones
            if _re.match(r"Noto (Sans|Serif) (?!Mono$|Display$)", family):
                continue
            if style not in ("Regular", "Bold", "Book", "Roman"):
                continue
            label = family if style in ("Regular", "Book", "Roman") else f"{family} {style}"
            _system.setdefault("sys:" + label, (label, path))
    return _system


def user_fonts():
    if not USER_FONT_DIR:
        return {}
    d = _Path(USER_FONT_DIR)
    return {"user:" + f.name: (f.stem, f) for f in sorted(d.glob("*")) if f.suffix.lower() in (".ttf", ".otf", ".pil")}


def font_list():
    out = [{"id": k, "label": label, "kind": "pixel"} for k, (label, _) in PIXEL_FONTS.items()]
    out += [{"id": k, "label": label, "kind": "uploaded"} for k, (label, _) in user_fonts().items()]
    out += [{"id": k, "label": label, "kind": "system"} for k, (label, _) in sorted(system_fonts().items(), key=lambda kv: kv[1][0].lower())]
    return out


def get_font(font_id, size=14):
    """Look up a font by id. Pixel fonts ignore `size` (use scale instead); others render at `size` dots."""
    if font_id in FONTS:
        return FONTS[font_id]
    key = (font_id, int(size))
    if key not in _cache:
        source = user_fonts().get(font_id) or system_fonts().get(font_id)
        if source is None:
            _log.warning("unknown font %r, using the pixel font", font_id)
            return NORMAL
        _cache[key] = OutlineFont(source[1], max(4, int(size)))
    return _cache[key]


def is_pixel(font_id):
    return font_id in FONTS
