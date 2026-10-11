// Mirror page: the silhouette mirror's camera, picture and takeover settings, and this browser's camera as a source.
// Frames go to the board as 84×42 gray dots over the WebSocket; the board keeps them in memory only.
import { $, api, autosave, board, bus, renderDots, rowsToGrid, safe, toast, wsSend, wsBacklog } from "./common.js";

const CAM_ID = Math.random().toString(36).slice(2, 10);
const FIELDS = {  // setting -> [element id, property, parse]
  source: ["mrSource", "value", String], device: ["mrDevice", "value", String], file: ["mrFile", "value", String],
  method: ["mrMethod", "value", String], sensitivity: ["mrSens", "value", Number],
  mode: ["mrMode", "value", String], trail_s: ["mrTrail", "value", Number],
  selfie: ["mrSelfie", "checked", Boolean], invert: ["mrInvert", "checked", Boolean],
  takeover: ["mrTakeover", "checked", Boolean], arm_s: ["mrArm", "value", Number],
  release_s: ["mrRelease", "value", Number], presence_pct: ["mrPresence", "value", Number], night: ["mrNight", "checked", Boolean],
};
let info = null, shown = false, pollTimer = null, saveTimer = null;
let stream = null, video = null, sendTimer = null, grab = null;

async function load() {
  info = await api("/api/mirror");
  const s = info.settings;
  const dev = $("mrDevice");
  const devices = info.detected.devices.length ? info.detected.devices : [{ path: s.device, name: "not found on this computer" }];
  dev.replaceChildren(...devices.map(d => new Option(`${d.path} (${d.name})`, d.path)));
  for (const [k, [id, prop]] of Object.entries(FIELDS)) $(id)[prop] = s[k];
  showFields();
}

function showFields() {
  const src = $("mrSource").value, mode = $("mrMode").value, d = info?.detected;
  document.querySelectorAll("#tab-mirror [data-show]").forEach(e => { e.hidden = e.dataset.show !== src; });
  document.querySelectorAll("#tab-mirror [data-show-mode]").forEach(e => { e.hidden = e.dataset.showMode !== mode; });
  $("mrSensVal").textContent = $("mrSens").value;
  const browser = src === "browser";
  $("mrCam").hidden = $("mrFacingRow").hidden = !browser;
  $("mrBg").hidden = $("mrMethod").value !== "background";
  if (d) {
    const parts = [d.ffmpeg ? "ffmpeg installed" : "ffmpeg not installed",
      d.devices.length ? `${d.devices.length} video device${d.devices.length > 1 ? "s" : ""}` : "no USB camera found",
      d.rpicam ? `${d.rpicam} found` : "no Pi camera tools"];
    $("mrDetected").textContent = browser ? "" : `On the board's computer: ${parts.join(", ")}.`;
  }
}

function save() {
  showFields();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(safe(async () => {
    const body = Object.fromEntries(Object.entries(FIELDS).map(([k, [id, prop, parse]]) => [k, parse($(id)[prop])]));
    if (body.source !== "browser") stopCamera();
    info = await autosave("mrSaved", () => api("/api/mirror", body));
    showFields();
  }), 300);
}

// ---- this browser's camera --------------------------------------------------------------

async function startCamera() {
  // tests can hand in a MediaStream (canvas.captureStream()) instead of a real camera
  stream = window.mirrorTestStream || await navigator.mediaDevices.getUserMedia({
    video: { facingMode: $("mrFacing").value, width: { ideal: 320 }, height: { ideal: 240 } }, audio: false });
  video ||= Object.assign(document.createElement("video"), { muted: true, playsInline: true });
  video.srcObject = stream;
  await video.play();
  grab ||= Object.assign(document.createElement("canvas"), { width: board.W, height: board.H }).getContext("2d", { willReadFrequently: true });
  sendTimer = setInterval(sendFrame, 100);
  $("mrCam").textContent = "Stop camera";
  $("mrCam").classList.remove("primary");
}

function stopCamera() {
  clearInterval(sendTimer);
  sendTimer = null;
  if (stream && stream !== window.mirrorTestStream) stream.getTracks().forEach(t => t.stop());
  stream = null;
  if (video) video.srcObject = null;
  $("mrCam").textContent = "Start camera";
  $("mrCam").classList.add("primary");
}

function sendFrame() {
  const vw = video.videoWidth, vh = video.videoHeight;
  if (!vw || !vh) return;
  if (wsBacklog() > 32768) return;  // the connection is behind: skip this frame
  const W = board.W, H = board.H;
  // the middle of the picture, cut to the board's shape
  const sw = Math.min(vw, vh * W / H), sh = sw * H / W;
  grab.drawImage(video, (vw - sw) / 2, (vh - sh) / 2, sw, sh, 0, 0, W, H);
  const px = grab.getImageData(0, 0, W, H).data;
  const gray = new Uint8Array(W * H);
  for (let i = 0; i < gray.length; i++) gray[i] = (px[i * 4] * 77 + px[i * 4 + 1] * 150 + px[i * 4 + 2] * 29) >> 8;
  let bin = "";
  for (let i = 0; i < gray.length; i++) bin += String.fromCharCode(gray[i]);
  const msg = { type: "camera", w: W, h: H, data: btoa(bin), id: CAM_ID, name: navigator.userAgent.includes("Mobile") ? "a phone" : "a browser" };
  if (!wsSend(msg)) api("/api/mirror/frame", msg).catch(() => {});
}

// ---- live preview and status ------------------------------------------------------------

async function poll() {
  if (!shown) return;
  try {
    const p = await api("/api/mirror/preview");
    const c = $("mrPreview");
    if (c.width !== board.W * 6) { c.width = board.W * 6; c.height = board.H * 6; }
    renderDots(c.getContext("2d"), rowsToGrid(p.rows), 6);
    $("mrStatus").textContent = statusText(p);
  } catch (e) {
    $("mrStatus").textContent = e.message;
  }
  pollTimer = setTimeout(poll, 250);
}

function statusText(p) {
  const src = $("mrSource").value;
  if (src === "off") return "The camera is off.";
  if (!p.live) {
    if (src === "browser") return sendTimer ? "Starting…" : "Start the camera to see yourself.";
    return p.error ? `The camera isn't sending pictures: ${p.error}` : "Waiting for the camera…";
  }
  const who = p.present ? "Someone is there" : "Nobody there";
  return `${who} · ${p.fg}% in front of the room · ${p.motion}% moving · ${p.ms} ms a frame`;
}

// ---- wiring -------------------------------------------------------------------------------

for (const [id] of Object.values(FIELDS)) $(id).addEventListener(id === "mrSens" ? "input" : "change", save);
$("mrCam").onclick = safe(async () => {
  if (sendTimer) return stopCamera();
  if (!window.mirrorTestStream && !navigator.mediaDevices?.getUserMedia) {
    throw new Error("This browser can't use a camera here. Cameras need https or localhost.");
  }
  try {
    await startCamera();
  } catch (e) {
    stopCamera();
    throw new Error(e.name === "NotAllowedError" ? "Camera access was refused. Allow it in the browser's site settings." : `Couldn't start the camera: ${e.message}`);
  }
});
$("mrFacing").onchange = safe(async () => { if (sendTimer) { stopCamera(); await startCamera(); } });
$("mrBg").onclick = safe(async () => {
  info = await api("/api/mirror/background", {});
  toast("Captured the empty room.");
});
$("mrShow").onclick = safe(async () => {
  await api("/api/run", { app: "mirror", transition: "dissolve" });
  toast("The mirror is on the board.");
});
window.addEventListener("pagehide", stopCamera);

bus.addEventListener("tabShown", e => {
  shown = e.detail === "mirror";
  clearTimeout(pollTimer);
  if (shown) load().then(poll).catch(err => toast(err.message, true));
});
