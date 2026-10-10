// Image & video tab: convert images, GIFs, video files and the webcam to dots.
import { $, api, board, bus, renderDots, gridToRows, safe, toast, refreshScenes, loadDrawings, wsSend } from "./common.js";

const MAX_FRAMES = 500;
const preview = $("preview"), pctx = preview.getContext("2d");
const video = $("video");
const work = document.createElement("canvas"), wctx = work.getContext("2d", { willReadFrequently: true });

let source = null;      // { kind: "image", frames: [{bitmap, delay}] } | { kind: "video"|"webcam" }
let converted = [];     // [{ grid, delay }]
let previewTimer = null, liveTimer = null, sourceName = "";

const fps = () => Math.max(1, Math.min(20, +$("fps").value || 10));

function convertFrame(src) {
  const W = board.W, H = board.H;
  const mode = $("mode").value, dither = $("dither").value === "dither";
  const threshold = +$("threshold").value, inv = $("invertImg").checked;
  const iw = src.videoWidth || src.width, ih = src.videoHeight || src.height;
  // art made for the board (its exact size or an exact multiple) is used dot for dot
  const factor = iw % W === 0 && ih % H === 0 && iw / W === ih / H ? iw / W : 0;
  if (factor >= 1) return nativeFrame(src, factor);
  work.width = W; work.height = H;
  wctx.fillStyle = "#000"; wctx.fillRect(0, 0, W, H);
  wctx.imageSmoothingQuality = "high";
  if (mode === "stretch") wctx.drawImage(src, 0, 0, W, H);
  else {
    const scale = mode === "crop" ? Math.max(W / iw, H / ih) : Math.min(W / iw, H / ih);
    const dw = iw * scale, dh = ih * scale;
    wctx.drawImage(src, (W - dw) / 2, (H - dh) / 2, dw, dh);
  }
  const d = wctx.getImageData(0, 0, W, H).data;
  const lum = new Float32Array(W * H);
  for (let i = 0; i < W * H; i++) lum[i] = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2];
  const grid = [];
  for (let y = 0; y < H; y++) {
    const row = [];
    for (let x = 0; x < W; x++) {
      const i = y * W + x, old = lum[i], on = old >= threshold;
      row.push(on !== inv);
      if (dither) {  // Floyd–Steinberg error diffusion
        const err = old - (on ? 255 : 0);
        if (x + 1 < W) lum[i + 1] += err * 7 / 16;
        if (y + 1 < H) {
          if (x > 0) lum[i + W - 1] += err * 3 / 16;
          lum[i + W] += err * 5 / 16;
          if (x + 1 < W) lum[i + W + 1] += err / 16;
        }
      }
    }
    grid.push(row);
  }
  return grid;
}

function nativeFrame(src, factor) {
  const W = board.W, H = board.H, inv = $("invertImg").checked;
  work.width = W * factor; work.height = H * factor;
  wctx.imageSmoothingEnabled = false;
  wctx.fillStyle = "#000"; wctx.fillRect(0, 0, work.width, work.height);
  wctx.drawImage(src, 0, 0);
  const d = wctx.getImageData(0, 0, work.width, work.height).data;
  const grid = [];
  for (let y = 0; y < H; y++) {
    const row = [];
    for (let x = 0; x < W; x++) {
      let sum = 0;  // average the factor x factor block
      for (let by = 0; by < factor; by++) for (let bx = 0; bx < factor; bx++) {
        const i = ((y * factor + by) * work.width + (x * factor + bx)) * 4;
        sum += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      }
      row.push((sum / (factor * factor) >= 128) !== inv);
    }
    grid.push(row);
  }
  wctx.imageSmoothingEnabled = true;
  return grid;
}

function showPreview(grid) {
  if (preview.width !== board.W * 8) { preview.width = board.W * 8; preview.height = board.H * 8; }
  renderDots(pctx, grid, 8);
}

function startPreview() {
  clearTimeout(previewTimer);
  if (source && source.kind !== "image" && !converted.length) {
    // live view of the video/webcam
    const step = () => {
      if (video.readyState >= 2) showPreview(convertFrame(video));
      previewTimer = setTimeout(step, 1000 / fps());
    };
    return step();
  }
  if (!converted.length) return;
  let i = 0;
  const step = () => {
    const f = converted[i];
    showPreview(f.grid);
    if (converted.length > 1) {
      i = (i + 1) % converted.length;
      previewTimer = setTimeout(step, Math.max(f.delay / +$("speed").value, 50));
    }
  };
  step();
}

function reconvert() {
  $("thVal").textContent = $("threshold").value;
  if (source?.kind === "image") converted = source.frames.map(f => ({ grid: convertFrame(f.bitmap), delay: f.delay }));
  updateButtons();
  startPreview();
}

function updateButtons() {
  const hasFrames = converted.length > 0;
  const isVideo = source && source.kind !== "image";
  $("send").disabled = !hasFrames;
  $("saveMedia").disabled = !hasFrames;
  $("addAsLayer").disabled = !hasFrames;
  $("live").disabled = !isVideo;
  $("clip").disabled = !isVideo;
  $("live").textContent = liveTimer ? "Stop streaming" : "Stream live";
  $("live").classList.toggle("primary", !!liveTimer);
}

function resetSource() {
  stopLive();
  if (source?.kind === "image") source.frames.forEach(f => f.bitmap.close());
  if (video.srcObject) { video.srcObject.getTracks().forEach(t => t.stop()); video.srcObject = null; }
  if (video.src) { URL.revokeObjectURL(video.src); video.removeAttribute("src"); video.load(); }
  source = null; converted = [];
}

async function decodeImage(file) {
  // Decode on the server (Pillow handles animated GIF/WebP/PNG reliably); fall back to the browser.
  try {
    const j = await api("/api/decode", file, { raw: true });
    return Promise.all(j.frames.slice(0, MAX_FRAMES).map(async f => ({
      bitmap: await createImageBitmap(await (await fetch("data:image/png;base64," + f.png)).blob()),
      delay: f.delay,
    })));
  } catch (err) {
    console.warn("server decode failed, using browser decode:", err);
    return [{ bitmap: await createImageBitmap(file), delay: 100 }];
  }
}

async function loadFile(file) {
  if (!file) return;
  bus.dispatchEvent(new CustomEvent("tab", { detail: "media" }));
  resetSource();
  sourceName = file.name || "pasted image";
  if (file.type.startsWith("video/")) {
    video.src = URL.createObjectURL(file);
    video.loop = true;
    await new Promise((ok, fail) => { video.onloadeddata = ok; video.onerror = () => fail(new Error("can't play this video")); });
    await video.play().catch(() => {});
    source = { kind: "video" };
    $("imgInfo").textContent = `${sourceName} · ${video.videoWidth}×${video.videoHeight} · ${video.duration.toFixed(1)} s — stream it live, or convert a clip`;
  } else if (file.type.startsWith("image/")) {
    $("imgInfo").textContent = "Decoding…";
    const frames = await decodeImage(file);
    source = { kind: "image", frames };
    const b = frames[0].bitmap;
    const native = b.width % board.W === 0 && b.height % board.H === 0 && b.width / board.W === b.height / board.H;
    $("imgInfo").textContent = `${sourceName} · ${b.width}×${b.height}` + (frames.length > 1 ? ` · ${frames.length} frames` : "")
      + (native ? " · board-size art: shown dot for dot" : "");
  } else {
    return toast("That isn't an image or video", true);
  }
  reconvert();
}

async function startWebcam() {
  if (!navigator.mediaDevices?.getUserMedia)
    return toast("Webcam needs https or localhost — open this page on the server machine via http://localhost:8080", true);
  resetSource();
  video.srcObject = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
  await video.play();
  source = { kind: "webcam" };
  sourceName = "webcam";
  $("imgInfo").textContent = "Webcam — stream it live, or record a clip";
  reconvert();
}

function stopLive() {
  clearInterval(liveTimer);
  liveTimer = null;
  updateButtons();
}

let liveDrawing = null;  // drawing layer that live video streams into

async function liveLayer(position) {
  // reuse the live layer if it's still on the board, otherwise make a new one in the right place
  if (liveDrawing && (board.runner?.drawings || []).includes(liveDrawing)) return liveDrawing;
  const j = await api("/api/drawings", { name: `Live: ${sourceName}`, show: true, position });
  await loadDrawings();
  return (liveDrawing = j.id);
}

async function toggleLive() {
  if (liveTimer) return stopLive();
  converted = [];
  const target = $("streamTarget").value;
  const layerId = target === "board" ? null : await liveLayer(target);
  let busy = false;
  liveTimer = setInterval(async () => {
    if (busy || video.readyState < 2) return;
    busy = true;
    const rows = gridToRows(convertFrame(video));
    try {
      if (!layerId) await api("/api/frame", { rows });
      else if (!wsSend({ type: "drawing_rows", drawing: layerId, rows })) await api(`/api/drawings/${layerId}`, { rows });
    } catch (e) { toast(e.message, true); stopLive(); }
    busy = false;
  }, 1000 / fps());
  if (layerId) toast("Streaming into a layer — what's running keeps going");
  updateButtons();
  startPreview();
}

async function convertClip() {
  stopLive();
  const secs = Math.max(1, +$("clipSecs").value || 10), rate = fps();
  const count = Math.min(MAX_FRAMES, Math.round(secs * rate));
  const frames = [];
  $("clip").disabled = true;
  try {
    if (source.kind === "video") {
      video.pause();
      // start at the current position, or earlier if there isn't enough video left
      const t0 = Math.max(0, Math.min(video.currentTime, video.duration - secs));
      for (let i = 0; i < count; i++) {
        const t = t0 + i / rate;
        if (t > video.duration) break;
        video.currentTime = t;
        await new Promise(ok => (video.onseeked = ok));
        frames.push({ grid: convertFrame(video), delay: Math.round(1000 / rate) });
        $("imgInfo").textContent = `Converting… ${i + 1}/${count}`;
      }
      video.play().catch(() => {});
    } else {
      for (let i = 0; i < count; i++) {
        frames.push({ grid: convertFrame(video), delay: Math.round(1000 / rate) });
        $("imgInfo").textContent = `Recording… ${((i + 1) / rate).toFixed(1)} / ${secs} s`;
        await new Promise(ok => setTimeout(ok, 1000 / rate));
      }
    }
  } finally {
    $("clip").disabled = false;
  }
  converted = frames;
  $("imgInfo").textContent = `${sourceName} clip · ${frames.length} frames at ${rate} fps — send it or save it as a scene`;
  updateButtons();
  startPreview();
}

const framesPayload = () => {
  const speed = +$("speed").value;
  return converted.map(f => ({ rows: gridToRows(f.grid), delay: Math.round(f.delay / speed) }));
};

$("send").onclick = safe(async () => {
  if (converted.length === 1) await api("/api/frame", { rows: gridToRows(converted[0].grid) });
  else await api("/api/animation", { loop: $("loop").checked, frames: framesPayload(), name: sourceName });
  toast("Sent to board");
});
$("saveMedia").onclick = safe(async () => {
  const name = prompt("Scene name?", sourceName.replace(/\.[^.]+$/, ""));
  if (!name) return;
  await api("/api/scenes", { name, frames: framesPayload() });
  await refreshScenes();
  toast(`Saved "${name}"`);
});
$("live").onclick = safe(toggleLive);
$("addAsLayer").onclick = safe(async () => {
  const position = $("layerPos").value;
  const name = sourceName.replace(/\.[^.]+$/, "") || "Image";
  if (converted.length === 1) {
    // a still picture becomes an editable drawing layer
    await api("/api/drawings", { name, rows: gridToRows(converted[0].grid), show: true, position });
    await loadDrawings();
  } else {
    // animations and clips become a saved scene, shown as a layer
    const { id } = await api("/api/scenes", { name, frames: framesPayload() });
    await refreshScenes();
    await api("/api/layers/add", { layer: { app: "scene", params: { scene: id, loop: $("loop").checked } }, position });
  }
  toast(`Added "${name}" as a layer ${position === "top" ? "on top" : "underneath"}`);
});
$("clip").onclick = safe(convertClip);
$("webcam").onclick = safe(startWebcam);

const drop = $("drop");
drop.onclick = () => $("file").click();
$("file").onchange = safe(e => loadFile(e.target.files[0]));
drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", safe(e => { e.preventDefault(); drop.classList.remove("over"); return loadFile(e.dataTransfer.files[0]); }));
document.addEventListener("paste", safe(e => {
  if (e.target.closest("input, textarea")) return;
  const item = [...e.clipboardData.items].find(i => i.type.startsWith("image/"));
  if (item) return loadFile(item.getAsFile());
}));
for (const id of ["mode", "dither", "threshold", "invertImg"]) $(id).addEventListener("input", reconvert);
$("speed").addEventListener("input", () => converted.length && startPreview());
$("fps").addEventListener("change", () => { if (liveTimer) { stopLive(); toggleLive(); } });
if (!window.isSecureContext) $("webcamNote").textContent = "(webcam only works on http://localhost or https)";
reconvert();
