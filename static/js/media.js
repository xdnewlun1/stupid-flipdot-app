// Import page: the one place to drop files. Photos and GIFs are converted to dots and saved to Pictures, videos
// go to the Videos library, several images or a .zip can become a slideshow; each then opens where it landed.
// A video file or the webcam can also be streamed live without saving.
import { $, api, ask, board, bus, choose, renderDots, gridToRows, safe, scenesCache, toast, refreshScenes, loadDrawings, uploadFile, wsSend, wsBacklog } from "./common.js";
import { importVideos } from "./videos.js";
import { importToSlideshow } from "./slideshows.js";
import { addToPlaylistButton } from "./addto.js";

const MAX_FRAMES = 500;
const preview = $("preview"), pctx = preview.getContext("2d");
const video = $("video");
const work = document.createElement("canvas"), wctx = work.getContext("2d", { willReadFrequently: true });

let source = null;      // { kind: "image", frames: [{bitmap, delay}] } | { kind: "video"|"webcam" }
let converted = [];     // [{ grid, delay }]
let previewTimer = null, liveTimer = null, sourceName = "";
let saved = null;       // { id, name }: the picture in Pictures made from the image loaded here; option changes update it

const fps = () => Math.max(1, Math.min(20, +$("fps").value || 10));

// This tab's live stream, so the status can say "from this browser" (other tabs and devices see another id)
export const STREAM_ID = Math.random().toString(36).slice(2, 12);
let streamSeen = false;  // the server has listed this stream as live (after that, its absence means it was stopped)
const streamInfo = () => ({ id: STREAM_ID, name: sourceName || "video" });

function convertFrame(src) {
  const W = board.W, H = board.H;
  const mode = $("imgMode").value, dither = $("dither").value === "dither";
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

// after an import, changing the options re-saves that picture (same id, so playlists and layers using it follow)
let resaveTimer = null;
function resave() {
  if (!saved || source?.kind !== "image" || !converted.length) return;
  clearTimeout(resaveTimer);
  const s = saved;
  resaveTimer = setTimeout(safe(async () => {
    $("imSaved").textContent = "Saving…";
    try {
      await api("/api/scenes", { id: s.id, name: s.name, frames: framesPayload() });
      $("imSaved").textContent = `Saved: "${s.name}" in Pictures is updated`;
    } catch (e) { $("imSaved").textContent = "Not saved"; throw e; }
    await refreshScenes();
  }), 400);
}

function updateButtons() {
  const hasFrames = converted.length > 0;
  const isVideo = source && source.kind !== "image";
  $("send").disabled = !hasFrames;
  $("saveMedia").disabled = !hasFrames;
  $("saveMedia").hidden = !!saved;
  $("openSaved").hidden = !saved;
  if ($("imToPlaylist")) $("imToPlaylist").hidden = !saved;
  $("addAsLayer").disabled = !hasFrames;
  $("live").disabled = !isVideo;
  $("clip").disabled = !isVideo;
  $("live").textContent = liveTimer ? "Stop streaming" : "Stream live";
  liveNote().hidden = !liveTimer;
  $("live").classList.toggle("primary", !!liveTimer);
}

function resetSource() {
  stopLive();
  if (source?.kind === "image") source.frames.forEach(f => f.bitmap.close());
  if (video.srcObject) { video.srcObject.getTracks().forEach(t => t.stop()); video.srcObject = null; }
  if (video.src) { URL.revokeObjectURL(video.src); video.removeAttribute("src"); video.load(); }
  source = null; converted = [];
  if (saved) { saved = null; bus.dispatchEvent(new Event("importReset")); }
  $("imSaved").textContent = "";
}

async function decodeImage(file, onprogress) {
  // Decode on the server (Pillow handles animated GIF/WebP/PNG reliably); fall back to the browser.
  try {
    const j = await uploadFile("/api/decode", file, onprogress);
    return Promise.all(j.frames.slice(0, MAX_FRAMES).map(async f => ({
      bitmap: await createImageBitmap(await (await fetch("data:image/png;base64," + f.png)).blob()),
      delay: f.delay,
    })));
  } catch (err) {
    console.warn("server decode failed, using browser decode:", err);
    try {
      return [{ bitmap: await createImageBitmap(file), delay: 100 }];
    } catch {
      throw new Error(`${file.name || "That file"} couldn't be read as an image (${err.message})`);
    }
  }
}

// a video file to stream live (or convert a clip of): played here in the browser, nothing uploaded
async function loadStream(file) {
  if (!file) return;
  resetSource();
  sourceName = file.name;
  video.src = URL.createObjectURL(file);
  video.loop = true;
  await new Promise((ok, fail) => { video.onloadeddata = ok; video.onerror = () => fail(new Error(`This browser can't play ${file.name}`)); });
  await video.play().catch(() => {});
  source = { kind: "video" };
  $("imgInfo").textContent = `${sourceName} · ${video.videoWidth}×${video.videoHeight} · ${video.duration.toFixed(1)} s. Not saved: stream it live, or convert a clip.`;
  reconvert();
  $("live").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// one image or GIF: decode, convert with the options on this page, save to Pictures. Returns the new picture's id.
async function importImage(file, label) {
  resetSource();
  sourceName = file.name || "Pasted image";
  progress(`Converting ${sourceName}${label}…`, 0);
  const frames = await decodeImage(file, (loaded, total) => progress(`Uploading ${sourceName}${label} to convert it…`, loaded / total));
  source = { kind: "image", frames };
  const b = frames[0].bitmap;
  const native = b.width % board.W === 0 && b.height % board.H === 0 && b.width / board.W === b.height / board.H;
  reconvert();
  const name = sourceName.replace(/\.[^.]+$/, "") || "Image";
  const { id } = await api("/api/scenes", { name, frames: framesPayload() });
  saved = { id, name };
  updateButtons();
  $("imgInfo").textContent = `${sourceName} · ${b.width}×${b.height}` + (frames.length > 1 ? ` · ${frames.length} frames` : "")
    + (native ? " · board-size art: shown dot for dot" : "") + `. Saved to Pictures as "${name}".`;
  return id;
}

const ext = f => (f.name.match(/\.([^.]+)$/)?.[1] || "").toLowerCase();
const isZip = f => ext(f) === "zip" || /zip/.test(f.type);
const isVideo = f => f.type.startsWith("video/") || ["webm", "mp4", "mov", "mkv", "m4v", "avi", "ogv", "mpg", "mpeg"].includes(ext(f));
const isImage = f => f.type.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "bmp"].includes(ext(f));
const stem = f => f.name.replace(/\.[^.]+$/, "");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function progress(text, frac = null, bad = false) {
  $("imProgressRow").hidden = !text;
  $("imProgressRow").classList.toggle("bad", bad);
  $("imStatus").textContent = text || "";
  $("imProgress").hidden = frac === null;
  if (frac !== null) {
    const pct = Math.round(100 * Math.max(0, Math.min(1, frac)));
    $("imProgress").firstChild.style.width = `${pct}%`;
    $("imProgress").setAttribute("aria-valuenow", pct);
  }
}

const go = tab => bus.dispatchEvent(new CustomEvent("tab", { detail: tab }));

// Everything dropped, chosen or pasted on Import comes through here and is sent where it belongs.
// An error also shows in the progress line, so it stays visible on this page.
async function importFiles(list) {
  try { await routeFiles(list); } catch (e) { progress(e.message, null, true); throw e; }
}

async function routeFiles(list) {
  const files = [...list].filter(Boolean);
  if (!files.length) return;
  const videos = files.filter(f => isVideo(f) && !isZip(f));
  const zips = files.filter(isZip);
  const images = files.filter(f => !isZip(f) && !isVideo(f) && isImage(f));
  const other = files.filter(f => !videos.includes(f) && !zips.includes(f) && !images.includes(f));
  if (other.length) toast(`Skipped ${other.map(f => f.name).join(", ")}: not a photo, GIF, video or .zip`, true);
  if (!videos.length && !zips.length && !images.length) return;

  // several pictures, or a zip: a slideshow, or (pictures only) each one into Pictures
  let pictures = images.length || zips.length ? "pictures" : null;
  if (zips.length || images.length > 1) {
    const what = [images.length ? plural(images.length, "image") : "", zips.length ? plural(zips.length, ".zip") : ""].filter(Boolean).join(" and ");
    if (zips.length === 1 && !images.length) pictures = "slideshow";
    else {
      pictures = await choose({
        title: `Import ${what}`,
        message: zips.length
          ? "They'll go into a new slideshow, which shows them one after another. (A .zip can only go into a slideshow.)"
          : "Make them a new slideshow that shows them one after another, or save each one to Pictures as its own picture.",
        choices: [...(zips.length ? [] : [["pictures", "Save each to Pictures"]]), ["slideshow", "Make a slideshow", true]],
      });
      if (!pictures) return videos.length ? sendVideos(videos, true) : undefined;
    }
  }
  if (videos.length) await sendVideos(videos, !pictures);
  if (pictures === "slideshow") {
    const first = zips[0] || images[0];
    const others = zips.length + images.length - 1;
    const name = others ? `${stem(first)} and ${others} more` : stem(first);
    go("slideshows");
    await importToSlideshow([...zips, ...images], name);
  } else if (pictures === "pictures") {
    const ids = [];
    try {
      for (const [n, f] of images.entries()) ids.push(await importImage(f, images.length > 1 ? ` (${n + 1} of ${images.length})` : ""));
    } catch (e) {
      progress(e.message, null, true);
      if (!ids.length) throw e;
      toast(e.message, true);
    }
    if (ids.length) {
      progress("");
      await refreshScenes();
      go("pictures");
      bus.dispatchEvent(new CustomEvent("picturesAdded", { detail: { ids, adjust: ids.at(-1) } }));
      toast(ids.length === 1 ? `Saved "${saved?.name}" to Pictures` : `Saved ${ids.length} pictures to Pictures`);
    }
  }
}

// videos go to the Videos library; with `land`, open it (otherwise say where they went)
async function sendVideos(videos, land) {
  if (land) {
    go("videos");
    await importVideos(videos);
  } else {
    await importVideos(videos);
    toast(`${plural(videos.length, "video")} uploading to Videos`, false, { label: "Open Videos", onclick: () => go("videos") }, 6000);
  }
}

async function startWebcam() {
  if (!navigator.mediaDevices?.getUserMedia)
    return toast("The webcam needs https or localhost. Open this page on the server machine at http://localhost:8080.", true);
  resetSource();
  video.srcObject = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
  await video.play();
  source = { kind: "webcam" };
  sourceName = "webcam";
  $("imgInfo").textContent = "Webcam. Stream it live, or record a clip.";
  reconvert();
}

function stopLive() {
  if (liveTimer) api("/api/stream/stop", { id: STREAM_ID }).catch(() => {});
  clearInterval(liveTimer);
  liveTimer = null;
  streamSeen = false;
  updateButtons();
}
// closing the tab ends the stream on the server right away (otherwise it notices after a few seconds without frames)
window.addEventListener("pagehide", () => {
  if (liveTimer) navigator.sendBeacon?.("/api/stream/stop", JSON.stringify({ id: STREAM_ID }));
});
// Stop pressed anywhere (or another browser started streaming): stop sending
bus.addEventListener("state", () => {
  if (!liveTimer) return;
  const mine = board.runner?.live?.id === STREAM_ID;
  if (mine) streamSeen = true;
  else if (streamSeen) {
    stopLive();
    toast("The live stream was stopped.");
  }
});

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
      const stream = streamInfo();
      if (wsBacklog() > 32768) { /* the connection is behind: drop this frame */ }
      else if (!layerId) {
        if (!wsSend({ type: "frame", rows, stream }) && (await api("/api/frame", { rows, stream })).stopped) stopLive();
      } else if (!wsSend({ type: "drawing_rows", drawing: layerId, rows, stream })) {
        if ((await api(`/api/drawings/${layerId}`, { rows, stream })).stopped) stopLive();
      }
    } catch (e) { toast(e.message, true); stopLive(); }
    busy = false;
  }, 1000 / fps());
  if (layerId) toast("Streaming into a layer. What's running keeps going.");
  updateButtons();
  startPreview();
}

function liveNote() {
  let n = $("liveNote");
  if (!n) {
    n = document.createElement("p");
    n.id = "liveNote";
    n.className = "muted";
    n.setAttribute("role", "status");
    n.textContent = "Streaming from this tab. It keeps going while you use other pages here, and stops when you close this tab.";
    $("live").closest(".row, div").after(n);
  }
  return n;
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
  $("imgInfo").textContent = `${sourceName} clip · ${frames.length} frames at ${rate} fps. Not saved yet: show it on the board or save it to Pictures.`;
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
  toast("Showing it on the board");
});
$("saveMedia").onclick = safe(async () => {
  const name = await ask({ title: "Save to Pictures", label: "Name", value: sourceName.replace(/\.[^.]+$/, ""), confirmText: "Save" });
  if (!name) return;
  const { id } = await api("/api/scenes", { name, frames: framesPayload() });
  await refreshScenes();
  toast(`Saved "${name}" to Pictures`, false, { label: "Open Pictures", onclick: () => {
    go("pictures");
    bus.dispatchEvent(new CustomEvent("picturesAdded", { detail: { ids: [id] } }));
  } }, 6000);
});
const imToPlaylist = addToPlaylistButton(() => saved && { app: "scene", params: { scene: saved.id } },
  { name: () => saved?.name || sourceName, duration: () => 30 });
imToPlaylist.id = "imToPlaylist";
imToPlaylist.hidden = true;
$("openSaved").after(imToPlaylist);
$("openSaved").onclick = () => {
  if (!saved) return;
  go("pictures");
  bus.dispatchEvent(new CustomEvent("picturesAdded", { detail: { ids: [saved.id], adjust: saved.id } }));
};
// the imported picture was deleted in Pictures: stop updating it
bus.addEventListener("scenes", () => {
  if (saved && !scenesCache.list.some(s => s.id === saved.id)) { saved = null; updateButtons(); $("imSaved").textContent = ""; }
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
    // animations and clips become a saved scene (the imported one, if there is one), shown as a layer
    const id = saved?.id || (await api("/api/scenes", { name, frames: framesPayload() })).id;
    await refreshScenes();
    await api("/api/layers/add", { layer: { app: "scene", params: { scene: id, loop: $("loop").checked } }, position });
  }
  toast(`Added "${name}" as a layer ${position === "top" ? "on top" : "underneath"}`);
});
$("clip").onclick = safe(convertClip);
$("webcam").onclick = safe(startWebcam);

const drop = $("drop");
drop.onclick = () => $("file").click();
drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } };
$("file").onchange = safe(async e => { const files = [...e.target.files]; e.target.value = ""; await importFiles(files); });
$("streamFile").onclick = () => $("streamFileInput").click();
$("streamFileInput").onchange = safe(async e => { const f = e.target.files[0]; e.target.value = ""; await loadStream(f); });
drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", safe(e => { e.preventDefault(); drop.classList.remove("over"); return importFiles(e.dataTransfer.files); }));
// paste an image anywhere (outside text fields) to import it
document.addEventListener("paste", safe(e => {
  if (e.target.closest("input, textarea, [contenteditable]")) return;
  const item = [...e.clipboardData.items].find(i => i.type.startsWith("image/"));
  if (!item) return;
  const f = item.getAsFile();
  return importFiles([new File([f], `Pasted image.${(f.type.split("/")[1] || "png").replace("jpeg", "jpg")}`, { type: f.type })]);
}));
for (const id of ["imgMode", "dither", "threshold", "invertImg"]) $(id).addEventListener("input", () => { reconvert(); resave(); });
$("speed").addEventListener("input", () => { if (converted.length) startPreview(); resave(); });
$("fps").addEventListener("change", () => { if (liveTimer) { stopLive(); toggleLive(); } });
if (!window.isSecureContext) $("webcamNote").textContent = "(webcam only works on http://localhost or https)";
reconvert();
