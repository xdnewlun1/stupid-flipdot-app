"""Wi-Fi QR code: phones scan it to join the network."""

import qr
from canvas import Canvas, wrap_text
from fonts import NORMAL, SMALL

from .base import App, P, register


def wifi_string(ssid, password="", security="WPA", hidden=False):
    """The de-facto standard Wi-Fi QR payload, e.g. WIFI:T:WPA;S:Home;P:secret;;"""
    esc = lambda s: "".join("\\" + ch if ch in '\\;,:"' else ch for ch in s)
    security = (security or "").upper()
    if security in ("", "NONE", "NOPASS", "OPEN"):
        out = f"WIFI:T:nopass;S:{esc(ssid)};"
    else:
        out = f"WIFI:T:{security};S:{esc(ssid)};P:{esc(password)};"
    if hidden:
        out += "H:true;"
    return out + ";"


def fit_qr(data, height, ecc="M", min_quiet=2):
    """(code, quiet, inverted): the smallest QR code for `data` that fits `height` dots with a lit border
    of at least `min_quiet` modules (up to 4). Falls back to ECC L if `ecc` needs too big a version, and
    then to a full-height inverted code (lit modules on black; the dark board around it is the border)."""
    max_version = max(1, (height - 2 * min_quiet - 17) // 4)
    for level in (ecc, "L"):
        try:
            code = qr.encode(data, ecc=level, max_version=max_version)
            return code, min(4, (height - code.size) // 2), False
        except ValueError:
            pass
    code = qr.encode(data, ecc="L", max_version=(height - 17) // 4)
    return code, 0, True


def _wrap(s, font, width):
    """Word wrap, breaking words that are too long on their own."""
    out = []
    for line in wrap_text(s, font, width):
        while font.width(line) > width and len(line) > 1:
            n = len(line)
            while n > 1 and font.width(line[:n]) > width:
                n -= 1
            out.append(line[:n])
            line = line[n:]
        out.append(line)
    return out


@register
class WifiQr(App):
    id = "wifi_qr"
    title = "Wi-Fi QR code"
    category = "Utilities"
    description = "A QR code guests scan with their phone camera to join your Wi-Fi."
    params = [
        P("ssid", "text", "", "Network name (SSID)"),
        P("password", "text", "", "Password"),
        P("security", "select", "WPA", options=[["WPA", "WPA/WPA2/WPA3"], ["WEP", "WEP"], ["none", "None (open network)"]]),
        P("hidden", "checkbox", False, "Hidden network"),
        P("show_name", "checkbox", True, "Show the network name next to the code"),
        P("show_password", "checkbox", False, "Show the password too"),
    ]
    interval = 1.0

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.picture = self._compose()

    def _compose(self):
        c = Canvas(self.w, self.h)
        ssid = str(self.p["ssid"] or "")
        if not ssid:
            c.text_center(10, "Wi-Fi QR", NORMAL)
            c.text_center(22, "SET THE NETWORK NAME", SMALL)
            return c
        try:
            code, quiet, inverted = fit_qr(wifi_string(ssid, str(self.p["password"] or ""), self.p["security"], self.p["hidden"]), self.h)
        except ValueError:
            c.text_center(12, "Too long for", NORMAL)
            c.text_center(22, "a QR code", NORMAL)
            return c
        side = code.size + 2 * quiet
        gap = 5 if inverted else 3  # an inverted code needs a wider black margin
        label = self._label(ssid, self.w - side - gap) if self.p["show_name"] else None
        if label:
            font, lines = label
            tw = max(font.width(l) for l in lines)
            x0 = (self.w - side - gap - tw) // 2
            qr.draw(c, code, x0, (self.h - side) // 2, quiet=quiet, invert=inverted)
            lh = font.height + 2
            y = (self.h - lh * len(lines) + 2) // 2
            for i, line in enumerate(lines):
                c.text(x0 + side + gap, y + i * lh, line, font)
        else:
            qr.draw(c, code, (self.w - side) // 2, (self.h - side) // 2, quiet=quiet, invert=inverted)
        return c

    def _label(self, ssid, width):
        """(font, lines) for the text beside the code, or None if it doesn't fit."""
        if width < 12:
            return None
        pw = str(self.p["password"] or "") if self.p["show_password"] and self.p["security"] != "none" else ""
        for font, max_lines in ((NORMAL, 4), (SMALL, 6)):
            lines = ["WI-FI" if font is SMALL else "Wi-Fi"] + _wrap(ssid, font, width)
            if pw:
                lines += _wrap(pw, font, width)
            if len(lines) <= max_lines and all(font.width(l) <= width for l in lines):
                return font, lines
        return None

    def render(self, c, now):
        c.px[:] = self.picture.px

    def describe(self):
        return f"Wi-Fi QR: {self.p['ssid']}" if self.p["ssid"] else self.title
