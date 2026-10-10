"""AI "Draw me": ask Claude (Messages API, plain urllib) for 1-bit pixel art that fits the board.

The model answers with frames of 0/1 rows; parse_frames() is lenient about the format and
repair() forces every frame to exactly W x H.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.request

log = logging.getLogger("aidraw")

DEFAULT_MODEL = "claude-opus-5-5"
MODELS = [
    ["claude-opus-5-5", "Claude Opus 5.5 (best results)"],
    ["claude-sonnet-5-5", "Claude Sonnet 5.5 (faster, cheaper)"],
    ["claude-haiku-5-5", "Claude Haiku 5.5 (fastest, cheapest)"],
    ["claude-fable-5-1", "Claude Fable 5.1 (most capable, most expensive)"],
]
# models that accept the server-side refusal fallback (retries a declined request on another model)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
API_VERSION = "2023-06-01"
MAX_FRAMES = 8
MAX_PROMPT = 500
TIMEOUT = 300


class AiError(Exception):
    """A problem worth showing to the user as-is."""

    def __init__(self, msg, status=502):
        super().__init__(msg)
        self.status = status


def base_url():
    # FLIPDOT_ANTHROPIC_BASE_URL lets tests point this at a local fake server
    return (os.environ.get("FLIPDOT_ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")


def build_prompt(prompt, frames, style, w, h):
    anim = (f"Draw an animation of exactly {frames} frames that loops smoothly; change only what needs to move "
            "between frames so it reads well when played back." if frames > 1 else "Draw exactly 1 frame.")
    return (
        f"You draw 1-bit pixel art for a {w}x{h} flip-dot display (each dot is either lit or dark; there is no grey).\n"
        f"Subject: {prompt}\n" + (f"Style: {style}\n" if style else "") + anim + "\n\n"
        "Make it bold and readable from across a room: use the whole display, thick shapes, clear outlines, "
        "and avoid single isolated dots and fine noise. Lettering only if asked for.\n\n"
        "Answer with only the frames, no commentary. Format each frame as a line `FRAME <n>` followed by exactly "
        f"{h} lines of exactly {w} characters, where `1` is a lit dot and `0` is a dark dot."
    )


def request_body(model, prompt, frames, style, w, h):
    body = {
        "model": model,
        "max_tokens": min(32000, 6000 + 3000 * frames),
        "output_config": {"effort": "low"},
        "messages": [{"role": "user", "content": build_prompt(prompt, frames, style, w, h)}],
    }
    if model in FALLBACK_MODELS:
        body["fallbacks"] = "default"
    return body


def call_api(api_key, body, timeout=TIMEOUT):
    headers = {"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
    if "fallbacks" in body:
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
    req = urllib.request.Request(base_url() + "/v1/messages", data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise AiError(_api_error(e.code, e.read())) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise AiError(f"Couldn't reach the Anthropic API: {reason}") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise AiError("The Anthropic API sent back something that isn't JSON.") from None


def _api_error(status, raw):
    try:
        err = json.loads(raw).get("error") or {}
        msg = err.get("message") or ""
    except (ValueError, AttributeError):
        msg = raw[:200].decode(errors="replace").strip()
    hint = {
        401: "The Anthropic API key was rejected; check it in Settings → AI drawing.",
        403: "This API key isn't allowed to use that model.",
        404: "The model wasn't found; pick another one in Settings → AI drawing.",
        429: "The Anthropic API is rate limiting this key; wait a minute and try again.",
        529: "The Anthropic API is overloaded right now; try again shortly.",
    }.get(status) or (f"The Anthropic API had a problem ({status}); try again shortly." if status >= 500
                      else f"The Anthropic API refused the request ({status}).")
    return f"{hint} ({msg})" if msg else hint


# ---- parsing ---------------------------------------------------------------------

ON, OFF = "1#Xx█@*", "0. _-"
_ROW = re.compile(r"^[01#Xx█@*. _-]+$")


def parse_frames(text):
    """Frames (lists of '0'/'1' strings) found in the model's text. Accepts FRAME markers, blank lines or
    code fences between frames, and #/. or X/. instead of 1/0."""
    frames, cur = [], []

    def close():
        if cur:
            frames.append(cur[:])
            cur.clear()
    for line in text.splitlines():
        s = line.strip().strip("|`")
        if re.fullmatch(r"(\S )+\S", s):  # "0 1 1 0" style rows
            s = s.replace(" ", "")
        if s.startswith("```") or not s:
            close()
            continue
        if re.match(r"^(frame|#+\s*frame)\b", s, re.I):
            close()
            continue
        # a pixel row: only on/off characters and at least a few of them (spaces inside count as off only if
        # the row also has 0/1 characters, so ordinary prose never matches)
        if len(s) >= 8 and _ROW.match(s) and sum(ch in "01#Xx█@*." for ch in s) >= len(s) * 0.8:
            cur.append("".join("1" if ch in ON else "0" for ch in s))
        else:
            close()
    close()
    # drop stray fragments (e.g. a lone row in a sentence) when there are real frames
    big = [f for f in frames if len(f) >= 4]
    return big or frames


def repair(rows, w, h):
    """Exactly h rows of w characters: rows of different widths are padded, too-big frames trimmed around the
    middle, too-small ones centred."""
    width = max(len(r) for r in rows)
    rows = [r.ljust(width, "0") for r in rows]
    if width > w:
        cut = (width - w) // 2
        rows = [r[cut:cut + w] for r in rows]
    elif width < w:
        left = (w - width) // 2
        rows = ["0" * left + r + "0" * (w - width - left) for r in rows]
    if len(rows) > h:
        cut = (len(rows) - h) // 2
        rows = rows[cut:cut + h]
    elif len(rows) < h:
        top = (h - len(rows)) // 2
        rows = ["0" * w] * top + rows + ["0" * w] * (h - len(rows) - top)
    return rows


def draw(settings, prompt, frames=1, style="", w=84, h=42, timeout=TIMEOUT):
    """Returns {"frames": [{"rows": [...]}], "repaired": n, "model": ...}. Raises AiError."""
    cfg = settings.get("ai") or {}
    key = (cfg.get("api_key") or "").strip()
    if not key:
        raise AiError("Add an Anthropic API key in Settings → AI drawing first.", 400)
    prompt = str(prompt or "").strip()
    if not prompt:
        raise AiError("Describe what to draw.", 400)
    if len(prompt) > MAX_PROMPT:
        raise AiError(f"Keep the description under {MAX_PROMPT} characters.", 400)
    try:
        frames = int(frames)
    except (TypeError, ValueError):
        frames = 1
    frames = max(1, min(MAX_FRAMES, frames))
    style = str(style or "").strip()[:200]
    model = cfg.get("model") or DEFAULT_MODEL

    resp = call_api(key, request_body(model, prompt, frames, style, w, h), timeout=timeout)
    if not isinstance(resp, dict) or "content" not in resp:
        raise AiError("The Anthropic API sent back an unexpected response.")
    stop = resp.get("stop_reason")
    if stop == "refusal":
        raise AiError("The model declined to draw that. Try describing it differently.", 422)
    text = "".join(b.get("text", "") for b in resp.get("content") or [] if isinstance(b, dict) and b.get("type") == "text")
    found = parse_frames(text)
    if not found:
        if stop == "max_tokens":
            raise AiError("The drawing was cut off before it was finished. Try fewer frames.")
        log.info("AI drawing: no frames in reply: %r", text[:300])
        raise AiError("The model didn't send back a drawing in the expected format. Try again.")
    if stop == "max_tokens" and len(found) > 1 and len(found[-1]) < h:
        found = found[:-1]  # the last frame was cut off mid-way
    found = found[:frames]
    repaired = sum(1 for f in found if len(f) != h or any(len(r) != w for r in f))
    return {"frames": [{"rows": repair(f, w, h)} for f in found], "repaired": repaired,
            "model": resp.get("model") or model, "requested": frames}
