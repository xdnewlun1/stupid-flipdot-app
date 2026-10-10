"""Board setup: identify panels on the board, validate a panel mapping and write it to config.yaml."""

import ipaddress
import re
from pathlib import Path

from canvas import Canvas
from fonts import SMALL

FIELDS = ("column", "row", "ip", "port", "address")


def panel_list(board):
    return [{"column": p.col + 1, "row": p.row + 1, "ip": p.ip, "port": p.port, "address": p.address, "rotate": p.rotate}
            for p in board.panels]


def validate(panels):
    """Check a mapping from the web page; returns it cleaned up (ints where numbers belong)."""
    if not isinstance(panels, list) or not panels:
        raise ValueError("add at least one panel")
    out, seen = [], {}
    for n, p in enumerate(panels, 1):
        try:
            q = {"column": int(p["column"]), "row": int(p["row"]), "ip": str(p["ip"]).strip(),
                 "port": int(p["port"]), "address": int(p["address"]), "rotate": int(p.get("rotate") or 0)}
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"panel {n}: column, row, port and address must be whole numbers") from None
        if not 1 <= q["column"] <= 32 or not 1 <= q["row"] <= 32:
            raise ValueError(f"panel {n}: column and row must be between 1 and 32")
        if not 1 <= q["port"] <= 65535:
            raise ValueError(f"panel {n}: port must be between 1 and 65535")
        if not 0 <= q["address"] <= 255:
            raise ValueError(f"panel {n}: address must be between 0 and 255")
        if q["rotate"] not in (0, 180):
            raise ValueError(f"panel {n}: rotate must be 0 or 180")
        try:
            ipaddress.ip_address(q["ip"])
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", q["ip"]):
                raise ValueError(f"panel {n}: {q['ip']!r} isn't an IP address or host name") from None
        key = (q["column"], q["row"])
        if key in seen:
            raise ValueError(f"panels {seen[key]} and {n} are both at column {key[0]}, row {key[1]}")
        seen[key] = n
        out.append(q)
    bus = {}
    for n, q in enumerate(out, 1):
        k = (q["ip"], q["port"], q["address"])
        if k in bus:
            raise ValueError(f"panels {bus[k]} and {n} both use {q['ip']}:{q['port']} address {q['address']}")
        bus[k] = n
    return out


def write_config(path, panels):
    """Rewrite the panels: block of config.yaml, keeping everything else (comments, top settings)."""
    path = Path(path)
    lines = path.read_text().splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"panels:\s*(#.*)?$", l)), None)
    if start is None:
        head, tail = lines + [""], []
    else:
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end].startswith((" ", "\t", "-"))):
            end += 1
        head, tail = lines[:start], lines[end:]
    block = ["panels:"]
    for n, p in enumerate(panels):
        if n:
            block.append("")
        block.append(f'  - column: "{p["column"]}"')
        for k in FIELDS[1:]:
            block.append(f'    {k}: "{p[k]}"')
        if p.get("rotate"):
            block.append(f'    rotate: "{p["rotate"]}"')
    text = "\n".join(head + block + ([""] + tail if tail else [])) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def identify_frames(board, index=None):
    """Frames that label panels in place (all of them, or just one with an outline): which config
    entry drives each physical panel. Text shows upside down on a panel that needs rotate: 180."""
    pw, ph = board.pw, board.ph
    targets = range(len(board.panels)) if index is None else [index]
    frames = []
    for page in range(3):
        c = Canvas(board.width, board.height)
        for i in targets:
            p = board.panels[i]
            x0, y0 = p.col * pw, p.row * ph
            label = (f"C{p.col + 1} R{p.row + 1}", f"{p.port}", f"ADR {p.address}")[page]
            if index is not None:
                c.rect(x0, y0, pw, ph)
            c.text_center(y0 + (ph - SMALL.height) // 2, label, SMALL, x0=x0, w=pw)
        frames.append({"rows": c.rows(), "delay": 1200})
    return frames
