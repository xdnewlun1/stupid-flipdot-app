"""Silhouette mirror: synthetic camera frames (a figure walking over a noisy room) through camera.py and apps/mirror.py.

Run: python3 -m pytest -q tests/test_mirror.py
"""

import base64
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

import apps  # noqa: E402
import camera  # noqa: E402
import rules  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402

W, H = 84, 42


def room(w=160, h=120, seed=1):
    """A static background: a gradient with some furniture."""
    img = Image.new("L", (w, h))
    img.putdata([int(90 + 80 * x / w + 30 * y / h) for y in range(h) for x in range(w)])
    d = ImageDraw.Draw(img)
    d.rectangle([w * 0.05, h * 0.55, w * 0.25, h * 0.95], fill=70)   # a cupboard
    d.rectangle([w * 0.7, h * 0.15, w * 0.85, h * 0.4], fill=220)    # a window
    return img


def frame(bg, t, rng, person=True, noise=10, x=None):
    """The room with a figure at horizontal position x (0..1, default walking with t) and sensor noise."""
    img = bg.copy()
    w, h = img.size
    if person:
        cx = (x if x is not None else 0.2 + 0.6 * ((t * 0.15) % 1.0)) * w
        d = ImageDraw.Draw(img)
        d.ellipse([cx - w * 0.06, h * 0.12, cx + w * 0.06, h * 0.32], fill=35)               # head
        d.rectangle([cx - w * 0.1, h * 0.32, cx + w * 0.1, h * 1.0], fill=40)                # body
        arm = h * 0.1 * (1 + (t * 2 % 1.0))
        d.line([cx + w * 0.1, h * 0.38, cx + w * 0.2, h * 0.38 + arm], fill=40, width=max(2, w // 40))  # a waving arm
    if noise:
        px = img.load()
        for _ in range(w * h // 3):
            i, j = rng.randrange(w), rng.randrange(h)
            px[i, j] = max(0, min(255, px[i, j] + rng.randint(-noise, noise)))
    return img


def store(**mirror):
    return SimpleNamespace(settings={"mirror": {**camera.DEFAULTS, **mirror}, "quiet_hours": {}}, dir=None)


def fresh(**mirror):
    cam = camera.Camera(store(**mirror), W, H)
    camera._cam = cam
    return cam


def flips(a, b):
    return sum(x != y for x, y in zip(a, b))


def run_mode(cam, mode, n=40, t0=1000.0, empty=8, person=True, fps=10, **extra):
    rng = random.Random(7)
    bg = room()
    app = apps.create(Context(W, H, cam.store), {"app": "mirror", "params": {"mode": mode, **extra}})
    frames = []
    c = Canvas(W, H)
    for i in range(n):
        now = t0 + i / fps
        cam.feed(frame(bg, i / fps, rng, person=person and i >= empty), now)
        app.render(c, now)
        frames.append(bytes(c.px))
    app.stop()
    return frames


def test_processor_finds_the_figure_and_ignores_noise():
    cam = fresh()
    rng = random.Random(3)
    bg = room()
    for i in range(10):
        cam.feed(frame(bg, 0, rng, person=False), 100 + i * 0.1)
    assert cam.latest(101).fg == 0, "an empty, noisy room must give an empty silhouette"
    for i in range(10):
        cam.feed(frame(bg, 0, rng, x=0.5), 101 + i * 0.1)
    s = cam.latest(102)
    assert 0.15 < s.fg < 0.5
    # the figure is in the middle columns, nothing at the edges
    rows = s.mask.load()
    assert rows[W // 2, H - 2] == 255 and rows[2, H - 2] == 0 and rows[W - 3, 5] == 0


def test_hysteresis_keeps_a_still_figure_steady():
    cam = fresh()
    rng = random.Random(5)
    bg = room()
    for i in range(10):
        cam.feed(frame(bg, 0, rng, person=False), 100 + i * 0.1)
    masks = []
    for i in range(30):
        cam.feed(frame(bg, 0, rng, x=0.5, noise=14), 101 + i * 0.1)
        masks.append(cam.latest(101 + i * 0.1).mask.tobytes())
    settled = masks[5:]
    total = sum(flips(a, b) for a, b in zip(settled, settled[1:]))
    assert total <= 6, f"a figure standing still flipped {total} dots over {len(settled)} frames"


def test_every_mode_draws_and_stays_calm():
    for mode in camera.MODES:
        cam = fresh()
        frames = run_mode(cam, mode)
        lit = sum(frames[-1])
        assert lit > 30, f"{mode}: nothing drawn"
        if mode not in ("life", "pixel"):  # the pixel mirror shows the room too
            empty = frames[:8]
            assert all(sum(f) == 0 for f in empty[2:]), f"{mode}: an empty room drew something"
        per = [flips(a, b) for a, b in zip(frames[10:], frames[11:])]
        assert max(per) < W * H * 0.3, f"{mode}: {max(per)} flips in one frame"


def test_invert_and_selfie():
    cam = fresh()
    plain = run_mode(cam, "solid", selfie="no")[-1]
    cam = fresh()
    inv = run_mode(cam, "solid", selfie="no", invert="yes")[-1]
    assert all(a != b for a, b in zip(plain, inv))
    cam = fresh()
    sel = run_mode(cam, "solid", selfie="yes")[-1]
    mirrored = b"".join(plain[y * W:(y + 1) * W][::-1] for y in range(H))
    assert flips(sel, mirrored) < W * H * 0.02


def test_no_camera_shows_the_setup_screen():
    cam = fresh(source="off")
    app = apps.create(Context(W, H, cam.store), {"app": "mirror"})
    c = Canvas(W, H)
    app.render(c, time.time())
    assert 50 < sum(c.px) < W * H // 3


def test_browser_frames_and_a_second_browser():
    cam = fresh()
    img = frame(room(W, H), 0, random.Random(1), person=False).tobytes()
    msg = {"w": W, "h": H, "data": base64.b64encode(img).decode(), "id": "tab1", "name": "Phone"}
    assert cam.push_browser(msg, 10.0)
    assert not cam.push_browser({**msg, "id": "tab2"}, 10.5), "a second browser can't take over a live camera"
    assert cam.push_browser({**msg, "id": "tab2"}, 13.0), "...but it can once the first has gone quiet"
    try:
        cam.push_browser({**msg, "id": "tab2", "data": base64.b64encode(img[:-1]).decode()}, 14.0)
        assert False
    except ValueError:
        pass
    cam = fresh(source="device")
    assert not cam.push_browser(msg, 10.0), "browser frames are ignored when the Pi's camera is the source"


class FakeRunner:
    def __init__(self):
        self.notif = None
        self.app = None
        self.live = None
        self.mode = "playlist"
        self.playlist = "Default"
        self.notified = []

    def notify(self, spec=None, app=None, duration=10, transition="dissolve", transition_duration=1.0, **kw):
        self.notified.append(spec)
        self.notif = {"app": apps.create(Context(W, H, camera._cam.store), spec), "until": self.now + duration}

    def tick(self, now):
        self.now = now
        if self.notif and now >= self.notif["until"]:
            self.notif["app"].stop()
            self.notif = None


class FakeAutomations:
    def __init__(self):
        self.events = []

    def presence(self, state, now=None):
        self.events.append((state, now))


def walk(cam, runner, t0, seconds, person, fps=10, seed=2):
    rng = random.Random(seed)
    bg = room()
    for i in range(int(seconds * fps)):
        now = t0 + i / fps
        runner.tick(now)
        cam.feed(frame(bg, now, rng, person=person, x=0.5), now)
    return t0 + seconds


def test_takeover_triggers_and_releases():
    cam = fresh(takeover=True, arm_s=1.5, release_s=5)
    runner, auto = FakeRunner(), FakeAutomations()
    tk = camera.Takeover(cam, runner, cam.store, auto)
    runner.now = 0
    t = walk(cam, runner, 1000, 3, person=False)
    assert not runner.notified and not cam.presence.present
    t = walk(cam, runner, t, 1.0, person=True)
    assert not runner.notified, "a moment in front of the camera isn't enough"
    t = walk(cam, runner, t, 2.0, person=True)
    assert len(runner.notified) == 1 and runner.notified[0]["app"] == "mirror" and tk.ours()
    assert auto.events and auto.events[0][0] == "arrive"
    t = walk(cam, runner, t, 10, person=True)
    assert tk.ours(), "stays while someone's there"
    assert len(runner.notified) == 1
    t = walk(cam, runner, t, 3, person=False)
    assert tk.ours(), "doesn't leave the moment they step away"
    t = walk(cam, runner, t, 4, person=False)
    assert runner.notif is None and not tk.ours(), "gives the board back after release_s"
    assert auto.events[-1][0] == "leave"


def test_takeover_respects_quiet_hours_and_games():
    cam = fresh(takeover=True, arm_s=0.5, release_s=3)
    cam.store.settings["quiet_hours"] = {"enabled": True, "start": "00:00", "end": "23:59"}
    runner = FakeRunner()
    camera.Takeover(cam, runner, cam.store)
    runner.now = 0
    t = walk(cam, runner, 1000, 2, person=False)
    walk(cam, runner, t, 3, person=True)
    assert not runner.notified, "quiet hours: no takeover"
    cam = fresh(takeover=True, arm_s=0.5, release_s=3, night=True)
    cam.store.settings["quiet_hours"] = {"enabled": True, "start": "00:00", "end": "23:59"}
    runner = FakeRunner()
    camera.Takeover(cam, runner, cam.store)
    runner.now = 0
    t = walk(cam, runner, 1000, 2, person=False)
    walk(cam, runner, t, 3, person=True)
    assert runner.notified, "allowed at night when the setting says so"
    cam = fresh(takeover=True, arm_s=0.5, release_s=3)
    runner = FakeRunner()
    runner.app = SimpleNamespace(id="tetris", interactive=True)
    camera.Takeover(cam, runner, cam.store)
    runner.now = 0
    t = walk(cam, runner, 1000, 2, person=False)
    walk(cam, runner, t, 3, person=True)
    assert not runner.notified, "doesn't interrupt a game"


def test_presence_rule_validates_and_fires():
    rs = rules.validate([{"trigger": {"type": "presence", "state": "leave"}, "action": {"type": "black"}},
                         {"trigger": {"type": "presence"}, "action": {"type": "black"}}])
    a = rules.Automations.__new__(rules.Automations)
    a.rules, a.last_fired, a.last_error = rs, {}, {}
    ran = []
    a.run_action = lambda act: ran.append(act)
    assert a.presence("arrive", 10.0) == [rs[1]["id"]]
    assert a.presence("leave", 10.0) == [rs[0]["id"]]
    try:
        rules.validate([{"trigger": {"type": "presence", "state": "maybe"}, "action": {"type": "black"}}])
        assert False
    except ValueError:
        pass


def test_file_source_through_ffmpeg():
    import shutil
    if not shutil.which("ffmpeg"):
        return
    cam = fresh(source="file", file="lavfi:testsrc2=size=320x240:rate=10")
    cam.acquire()
    try:
        deadline = time.time() + 8
        while cam.latest() is None and time.time() < deadline:
            time.sleep(0.1)
        s = cam.latest()
        assert s is not None, f"no frames from ffmpeg ({cam.error})"
        assert s.gray.size == (W, H)
    finally:
        cam.release()
        cam.stop()
    assert not cam.status()["running"]
