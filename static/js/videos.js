// Videos page: upload clips (converted on the server with ffmpeg), pick one, play it on the board, scrub through it.
import { $, api, ask, autosave, board, bus, el, icon, iconButton, renderDots, rowsToGrid, safe, toast, undoable, videosCache } from "./common.js";

let lib = { videos: [], ffmpeg: true, settings: {} };
let current = null;          // id of the video in the player panel
let playhead = 0;            // seconds, in the panel
let dragging = false;        // the timeline (or a range handle) is being dragged
let pollTimer = null;
const uploads = [];          // { name, loaded, total, error, done }
const fresh = new Set();     // videos just added from Import: highlighted in the grid until another page is opened

export const fmtTime = s => {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
};
const fmtSize = b => b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`;
const MODES = { crop: "Cut to fit", fit: "Fit inside (black bars)", stretch: "Stretch to fit" };
const cur = () => lib.videos.find(v => v.id === current) || null;
const onBoard = () => board.runner?.video && board.runner.video.id === current && !board.runner.notification ? board.runner.video : null;

export async function loadVideos() {
  lib = await api("/api/videos");
  videosCache.list = lib.videos;
  if (current && !cur()) current = null;
  bus.dispatchEvent(new Event("videos"));
  render();
  const busy = lib.videos.some(v => v.status === "queued" || v.status === "converting");
  clearTimeout(pollTimer);
  if (busy) pollTimer = setTimeout(() => loadVideos().catch(() => {}), 1000);
}

// ---- library grid ----

function dots(canvas, rows, cell) {
  if (!rows) return;
  const grid = rowsToGrid(rows);
  if (canvas.width !== grid[0].length * cell) { canvas.width = grid[0].length * cell; canvas.height = grid.length * cell; }
  renderDots(canvas.getContext("2d"), grid, cell, { seams: false });
}

const native = v => !!v?.native;
// a source that's a whole multiple of the board (e.g. 168×84) is still scaled like any video: just say so
function sourceNote(v) {
  if (native(v) || !v.source_w) return "";
  const k = v.source_w / board.W;
  return Number.isInteger(k) && k > 1 && v.source_h === board.H * k ? ` · source ${v.source_w}×${v.source_h} (${k}× board size, scaled)` : "";
}
const fpsText = f => (Math.abs(f - Math.round(f)) < 0.01 ? Math.round(f) : f.toFixed(2));

function statusLine(v) {
  if (v.status === "queued") return "Waiting to convert";
  if (v.status === "converting") return v.progress != null ? `Converting… ${Math.round(v.progress * 100)}%` : "Converting…";
  if (v.status === "failed") return v.error || "Conversion failed";
  return `${fmtTime(v.duration)} · ${fmtSize(v.bytes || 0)}`;
}

function card(v) {
  const c = el("canvas", { width: 84 * 3, height: 42 * 3, "aria-hidden": "true" });
  if (v.poster) dots(c, v.poster, 3);
  const playing = board.runner?.video?.id === v.id;
  const ready = v.status === "ready";
  const bar = v.status === "converting" || v.status === "queued"
    ? el("div", { class: "vdBar", role: "progressbar", "aria-label": `Converting ${v.name}`, "aria-valuemin": 0, "aria-valuemax": 100,
                  "aria-valuenow": Math.round((v.progress || 0) * 100) }, el("span", { style: `width:${(v.progress || 0) * 100}%` }))
    : null;
  return el("div", { class: "ssItem vdItem" + (v.id === current ? " selected" : "") + (playing ? " playing" : "") + (fresh.has(v.id) ? " justAdded" : ""), "data-id": v.id },
    el("button", { class: "vdPick", "aria-label": `Open ${v.name}`, "aria-pressed": String(v.id === current), onclick: () => select(v.id) }, c),
    el("div", { class: "name", title: v.name }, v.name),
    native(v) ? el("span", { class: "vdBadge", title: "Board-size video: shown dot for dot" }, "84×42 · dot for dot") : null,
    el("div", { class: "vdMeta" + (v.status === "failed" ? " bad" : "") }, statusLine(v)),
    bar,
    el("div", { class: "ctl" },
      ready ? el("button", { class: "playNow", title: "Show on board", "aria-label": `Show ${v.name} on the board`, onclick: safe(async () => {
        select(v.id);
        await play(v.id, 0);
      }) }, icon("play")) : null,
      el("span", { class: "spacer" }),
      iconButton("edit", `Rename ${v.name}`, safe(() => rename(v))),
      iconButton("x", `Delete ${v.name}`, safe(() => remove(v)), "danger")));
}

function render() {
  $("vdNoFfmpeg").hidden = lib.ffmpeg;
  if (!lib.ffmpeg) $("vdNoFfmpeg").replaceChildren(icon("warn"), el("span", {}, lib.no_ffmpeg));
  $("vdDrop").classList.toggle("disabled", !lib.ffmpeg);
  $("vdEmpty").hidden = lib.videos.length > 0 || uploads.some(u => !u.done);
  $("vdGrid").replaceChildren(...lib.videos.map(card));
  renderUploads();
  renderOptions();
  renderPanel();
}

// ---- uploads: one at a time, with a progress bar each ----

function renderUploads() {
  $("vdUploads").replaceChildren(...uploads.filter(u => !u.done || u.error).map(u => el("div", { class: "vdUpload" + (u.error ? " bad" : "") },
    el("span", { class: "vdUpName" }, u.name),
    u.error ? el("span", {}, u.error) : el("div", { class: "vdBar", role: "progressbar", "aria-label": `Uploading ${u.name}`,
      "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(100 * u.loaded / (u.total || 1)) },
      el("span", { style: `width:${100 * u.loaded / (u.total || 1)}%` })),
    u.error ? null : el("span", { class: "muted" }, u.loaded >= u.total ? "Uploaded, starting…" : `${fmtSize(u.loaded)} of ${fmtSize(u.total)}`),
    u.error ? el("button", { type: "button", class: "close", "aria-label": "Dismiss", onclick: () => { u.done = true; u.error = null; renderUploads(); } }, "×") : null)));
}

let uploading = Promise.resolve();
function addFiles(files, { focus = false } = {}) {
  if (!lib.ffmpeg) return toast(lib.no_ffmpeg, true);
  const max = (lib.settings.max_upload_mb || 1024) * 1024 * 1024;
  for (const f of files) {
    const u = { name: f.name, loaded: 0, total: f.size, done: false, error: null, focus };
    uploads.push(u);
    if (f.size > max) { u.error = `${fmtSize(f.size)} is over the ${lib.settings.max_upload_mb} MB limit (Options, below).`; continue; }
    uploading = uploading.then(() => upload(f, u));
  }
  renderUploads();
}

function upload(file, u) {
  const q = new URLSearchParams({ name: file.name, mode: $("vdMode").value, keep: $("vdKeep").checked ? "1" : "0" });
  return new Promise(resolve => {
    const x = new XMLHttpRequest();
    x.open("POST", `/api/videos/upload?${q}`);
    x.upload.onprogress = e => { u.loaded = e.loaded; u.total = e.total || u.total; renderUploads(); };
    x.onload = () => {
      let j = {};
      try { j = JSON.parse(x.responseText); } catch {}
      if (x.status >= 400) u.error = `${file.name}: ${j.error || `upload failed (HTTP ${x.status})`}`;
      else {
        u.done = true;
        if (!current || u.focus) current = j.id;
        if (u.focus) fresh.add(j.id);
      }
      loadVideos().then(() => { if (u.focus && !u.error && j.id) reveal(j.id); }).catch(() => {}).finally(resolve);
    };
    x.onerror = () => { u.error = `${file.name}: the upload was cut off. Check the connection and try again.`; renderUploads(); resolve(); };
    x.send(file);
  });
}

$("vdDrop").onclick = () => $("vdFile").click();
$("vdDrop").onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("vdFile").click(); } };
$("vdFile").onchange = () => { addFiles([...$("vdFile").files]); $("vdFile").value = ""; };
$("vdDrop").addEventListener("dragover", e => { e.preventDefault(); $("vdDrop").classList.add("over"); });
$("vdDrop").addEventListener("dragleave", () => $("vdDrop").classList.remove("over"));
$("vdDrop").addEventListener("drop", e => { e.preventDefault(); $("vdDrop").classList.remove("over"); addFiles([...e.dataTransfer.files]); });

// From Import: upload these into the library, then show each one selected, with its conversion progress.
export async function importVideos(files) {
  await loadVideos();
  addFiles(files, { focus: true });
  requestAnimationFrame(() => $("vdUploads").scrollIntoView({ block: "center", behavior: "smooth" }));
}

// scroll the video's card into view and open it in the player panel
function reveal(id) {
  if ($("tab-videos").hidden) return;
  select(id, false);
  $("vdPlayer").scrollIntoView({ block: "start", behavior: "smooth" });
}

// ---- rename / delete ----

async function rename(v) {
  const name = await ask({ title: "Rename video", value: v.name, confirmText: "Rename" });
  if (!name || name === v.name) return;
  await api(`/api/videos/${v.id}`, { name });
  await loadVideos();
  toast("Renamed");
}

async function remove(v) {
  await api(`/api/videos/${v.id}/delete`, {});
  if (current === v.id) current = null;
  await loadVideos();
  undoable(`Deleted ${v.name}`, { undo: async () => {
    await api(`/api/videos/${v.id}/restore`, {});
    current = v.id;
    await loadVideos();
    toast(`${v.name} is back`);
  } });
}

// ---- the player panel ----

let strip = [], panelFor = null, stripKey = "";
function select(id, scroll = true) {
  current = id;
  const v = cur();
  playhead = onBoard() ? onBoard().pos : (v?.range?.start || 0);
  render();
  if (scroll) $("vdPlayer").scrollIntoView({ block: "nearest" });
}

const displayQuery = v => new URLSearchParams(Object.entries(v.display || {}).map(([k, x]) => [k, String(x)])).toString();

function renderPanel() {
  const v = cur();
  $("vdPlayer").hidden = !v;
  if (!v) { panelFor = null; return; }
  const ready = v.status === "ready";
  $("vdName").textContent = v.name;
  $("vdNative").hidden = !native(v);
  $("vdInfo").textContent = ready
    ? `${fmtTime(v.duration)} · ${fpsText(v.fps)} frames a second · ${fmtSize(v.bytes)} (about ${v.mb_per_hour} MB an hour) · ` +
      (native(v) ? "board size, shown dot for dot" : MODES[v.mode] || v.mode) + sourceNote(v) + (v.has_original ? " · original kept" : "")
    : statusLine(v);
  $("vdNativeNote").hidden = !native(v) || !ready;
  $("vdLook").classList.toggle("native", native(v));
  $("vdLook").querySelector("summary").textContent = native(v) ? "Display options (Negative only)" : "Display options";
  renderTitleMode(v);
  $("vdBody").hidden = !ready;
  const conv = v.status === "converting" || v.status === "queued";
  $("vdConvBar").hidden = !conv;
  if (conv) {
    $("vdConvBar").firstChild.style.width = `${(v.progress || 0) * 100}%`;
    $("vdConvBar").setAttribute("aria-valuenow", Math.round((v.progress || 0) * 100));
  }
  if (panelFor !== v.id + v.status) {  // filled again once it's converted: the title is known by then
    panelFor = v.id + v.status;
    $("vdTitle").value = v.title || "";
    $("vdTitleNote").textContent = v.meta_title ? `From the file: "${v.meta_title}"` : "The file has no title of its own.";
    const d = v.display || {};
    $("vdDither").value = d.dither || "ordered";
    $("vdThreshold").value = d.threshold ?? 128;
    $("vdContrast").value = d.contrast ?? 1;
    $("vdGamma").value = d.gamma ?? 1;
    $("vdInvert").checked = !!d.invert;
    $("vdLoop").checked = !!v.range?.loop;
    $("vdRemode").value = v.mode;
    showDisplayValues();
  }
  $("vdReconvert").hidden = !v.has_original || native(v);
  if (!ready) return;
  $("vdSeek").max = v.duration;
  const key = v.id + displayQuery(v);
  if (key !== stripKey) { stripKey = key; loadStrip(v).catch(() => {}); }
  renderTime();
}

async function loadStrip(v) {
  strip = (await api(`/api/videos/${v.id}/strip?${displayQuery(v)}`)).frames;
  $("vdStrip").replaceChildren(...strip.map(f => {
    const c = el("canvas", { "aria-hidden": "true" });
    dots(c, f.rows, 2);
    return el("button", { class: "vdThumb", title: `Go to ${fmtTime(f.t)}`, "aria-label": `Go to ${fmtTime(f.t)}`, onclick: safe(() => seekTo(f.t, true)) }, c);
  }));
  preview(playhead);
}

function range(v = cur()) {
  const r = v?.range || {};
  const end = r.end > 0 && r.end <= v.duration ? r.end : v.duration;
  return { start: Math.min(r.start || 0, end), end, loop: !!r.loop, set: (r.start || 0) > 0 || (r.end > 0 && r.end < v.duration) };
}

function renderTime() {
  const v = cur();
  if (!v || v.status !== "ready") return;
  const live = onBoard();
  if (live && !dragging && Date.now() > holdUntil) playhead = live.pos;
  if (!dragging) $("vdSeek").value = playhead;
  $("vdSeek").setAttribute("aria-valuetext", `${fmtTime(playhead)} of ${fmtTime(v.duration)}`);
  $("vdTime").textContent = `${fmtTime(playhead)} / ${fmtTime(v.duration)}`;
  const r = range(v), pct = t => `${(100 * t / (v.duration || 1)).toFixed(3)}%`;
  $("vdRange").style.left = pct(r.start);
  $("vdRange").style.width = `calc(${pct(r.end)} - ${pct(r.start)})`;
  $("vdRange").hidden = !r.set;
  $("vdIn").style.left = pct(r.start);
  $("vdOut").style.left = pct(r.end);
  $("vdRangeText").textContent = r.set ? `Range ${fmtTime(r.start)} to ${fmtTime(r.end)}${r.loop ? ", looping" : ""}` : "No range: plays the whole video";
  $("vdClearRange").disabled = !r.set;
  const paused = live && board.runner.paused;
  $("vdPlayPause").querySelector("use").setAttribute("href", live && !paused ? "#i-pause" : "#i-play");
  $("vdPlayPause").querySelector("span").textContent = live && !paused ? "Pause" : live ? "Resume" : "Play";
  $("vdPlayPause").setAttribute("aria-pressed", String(!!paused));
  $("vdOnBoard").textContent = live ? `On the board${paused ? ", paused" : ""}${live.ended ? ", finished" : ""}` : "Not on the board";
  $("vdStop").disabled = !live;
  if (!dragging) {
    if (live && !board.runner.notification) showBoard();
  }
}

// the preview: the board itself while this video plays there, otherwise the frame at the playhead
function showBoard() {
  if (!board.grid.length) return;
  const c = $("vdPreview");
  if (c.width !== board.W * 6) { c.width = board.W * 6; c.height = board.H * 6; }
  renderDots(c.getContext("2d"), board.grid, 6, { seams: false });
}

let previewBusy = false, previewWant = null;
async function preview(t) {
  const v = cur();
  if (!v || v.status !== "ready") return;
  if (onBoard() && !dragging) return showBoard();
  previewWant = t;
  if (previewBusy) return;  // the latest position is fetched when this one comes back
  previewBusy = true;
  try {
    while (previewWant !== null) {
      const want = previewWant;
      previewWant = null;
      const j = await api(`/api/videos/${v.id}/frame?t=${want.toFixed(2)}&${displayQuery(v)}`);
      dots($("vdPreview"), j.rows, 6);
    }
  } catch (e) { /* a missed preview frame isn't worth an error */ } finally { previewBusy = false; }
}

async function play(id, t) {
  const v = lib.videos.find(x => x.id === id);
  await api(`/api/videos/${id}/play`, { t, speed: +$("vdSpeed").value || 1 });
  toast(`Showing ${v?.name || "the video"}`);
}

async function control(body) {
  return api("/api/videos/control", { id: current, ...body });
}

let holdUntil = 0;  // just after a seek, the board's position is still the old one: keep the playhead where it was put
async function seekTo(t, commit) {
  const v = cur();
  const target = playhead = Math.max(0, Math.min(v.duration, t));
  if (commit && onBoard()) holdUntil = Date.now() + 1000;
  renderTime();
  preview(target);
  if (commit && onBoard()) await control({ seek: target });
}

// timeline: dragging previews, letting go seeks the board
const seek = $("vdSeek");
seek.addEventListener("input", () => { dragging = true; playhead = +seek.value; renderTime(); preview(playhead); });
seek.addEventListener("change", safe(async () => { dragging = false; await seekTo(+seek.value, true); }));
seek.addEventListener("pointerup", () => { dragging = false; });
seek.addEventListener("keydown", e => {
  const d = { ArrowLeft: -1, ArrowRight: 1, ArrowDown: -1, ArrowUp: 1 }[e.key];
  if (!d) return;
  e.preventDefault();
  e.stopPropagation();  // not a game key
  safe(() => seekTo(playhead + d * (e.shiftKey ? 10 : 1), true))();
});

// range handles: drag along the timeline
for (const [id, which] of [["vdIn", "start"], ["vdOut", "end"]]) {
  const h = $(id);
  const timeAt = e => {
    const box = $("vdTrack").getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)) * cur().duration;
  };
  h.addEventListener("pointerdown", e => { h.setPointerCapture(e.pointerId); dragging = true; e.preventDefault(); });
  h.addEventListener("pointermove", e => {
    if (!h.hasPointerCapture(e.pointerId)) return;
    const v = cur(), r = range(v), t = timeAt(e);
    v.range = { ...v.range, start: which === "start" ? Math.min(t, r.end - 0.5) : r.start, end: which === "end" ? Math.max(t, r.start + 0.5) : r.end };
    playhead = which === "start" ? v.range.start : v.range.end;
    renderTime();
    preview(playhead);
  });
  h.addEventListener("pointerup", safe(async e => {
    if (!h.hasPointerCapture(e.pointerId)) return;
    dragging = false;
    await saveRange();
  }));
  h.addEventListener("keydown", e => {
    const d = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
    if (!d) return;
    e.preventDefault(); e.stopPropagation();
    const v = cur(), r = range(v), step = d * (e.shiftKey ? 10 : 1);
    v.range = { ...v.range, start: r.start, end: r.end, [which]: Math.max(0, Math.min(v.duration, r[which] + step)) };
    playhead = v.range[which];
    renderTime(); preview(playhead);
    safe(saveRange)();
  });
}

async function saveRange() {
  const v = cur(), r = range(v);
  const body = { start: Math.round(r.start * 100) / 100, end: r.end >= v.duration ? 0 : Math.round(r.end * 100) / 100, loop: $("vdLoop").checked };
  v.range = body;
  await autosave("vdRangeSaved", () => api(`/api/videos/${v.id}`, { range: body }));
  if (onBoard()) await control(body);
  renderTime();
}

$("vdSetIn").onclick = safe(async () => {
  const v = cur(), r = range(v);
  v.range = { ...v.range, start: playhead, end: playhead < r.end ? r.end : 0 };
  await saveRange();
});
$("vdSetOut").onclick = safe(async () => {
  const v = cur(), r = range(v);
  if (playhead <= r.start + 0.2) throw new Error("Move the playhead after the start of the range first.");
  v.range = { ...v.range, start: r.start, end: playhead };
  await saveRange();
});
$("vdClearRange").onclick = safe(async () => { cur().range = { start: 0, end: 0, loop: $("vdLoop").checked }; await saveRange(); });
$("vdLoop").onchange = safe(saveRange);
$("vdPlayRange").onclick = safe(async () => { const v = cur(); await play(v.id, range(v).start); });

// playback buttons
$("vdShow").onclick = safe(async () => {
  const v = cur();
  const r = range(v);
  const from = playhead >= r.end - 0.5 || playhead < r.start ? r.start : playhead;
  await play(v.id, from);
});
$("vdPlayPause").onclick = safe(async () => {
  const live = onBoard();
  if (!live) return $("vdShow").click();
  if (live.ended) return play(current, range().start);
  await api("/api/pause", { paused: !board.runner.paused });
});
$("vdStop").onclick = safe(() => api("/api/stop", {}));
$("vdBack").onclick = safe(() => onBoard() ? control({ nudge: -10 }) : seekTo(playhead - 10));
$("vdFwd").onclick = safe(() => onBoard() ? control({ nudge: 10 }) : seekTo(playhead + 10));
const step = d => safe(async () => {
  const ready = lib.videos.filter(v => v.status === "ready");
  if (!ready.length) return;
  if (onBoard() && board.runner.app === "video") {
    followNext = true;
    return api(d > 0 ? "/api/next" : "/api/prev", {});
  }
  const i = ready.findIndex(v => v.id === current);
  select(ready[(i + d + ready.length) % ready.length].id);
});
let followNext = false;
$("vdPrev").onclick = step(-1);
$("vdNext").onclick = step(1);
$("vdSpeed").onchange = safe(async () => { if (onBoard()) await control({ speed: +$("vdSpeed").value }); });

// whether the title shows when it starts: this video's choice, else the setting at the top
function renderTitleMode(v) {
  const all = lib.settings?.show_title !== false;
  $("vdTitleMode").options[0].textContent = native(v) ? "Default: off (board-size video)" : `As set for all videos (${all ? "on" : "off"})`;
  if (document.activeElement !== $("vdTitleMode")) $("vdTitleMode").value = v.show_title || "auto";
}
$("vdTitleMode").onchange = safe(() => autosave("vdTitleModeSaved", async () => {
  const v = cur();
  await api(`/api/videos/${v.id}`, { show_title: $("vdTitleMode").value });
  v.show_title = $("vdTitleMode").value;
}));
$("vdShowTitles").onchange = safe(() => autosave("vdShowTitlesSaved", async () => {
  await api("/api/settings", { videos: { show_title: $("vdShowTitles").checked } });
  lib.settings = { ...lib.settings, show_title: $("vdShowTitles").checked };
  if (cur()) renderTitleMode(cur());
}));

// title shown when it starts
$("vdTitle").onchange = safe(() => autosave("vdTitleSaved", async () => {
  await api(`/api/videos/${current}`, { title: $("vdTitle").value });
  cur().title = $("vdTitle").value.trim();
}));
$("vdTitleClear").onclick = safe(async () => { $("vdTitle").value = ""; $("vdTitle").dispatchEvent(new Event("change")); });

// display options: saved with the video, applied on the board right away
function showDisplayValues() {
  $("vdThVal").textContent = $("vdThreshold").value;
  $("vdConVal").textContent = (+$("vdContrast").value).toFixed(1);
  $("vdGamVal").textContent = (+$("vdGamma").value).toFixed(1);
}
let displayTimer = null;
for (const id of ["vdDither", "vdThreshold", "vdContrast", "vdGamma", "vdInvert"]) {
  $(id).addEventListener("input", () => {
    showDisplayValues();
    const v = cur();
    v.display = { dither: $("vdDither").value, threshold: +$("vdThreshold").value, contrast: +$("vdContrast").value,
                  gamma: +$("vdGamma").value, invert: $("vdInvert").checked };
    preview(playhead);
    clearTimeout(displayTimer);
    displayTimer = setTimeout(safe(() => autosave("vdDisplaySaved", async () => {
      await api(`/api/videos/${v.id}`, { display: v.display });
      stripKey = "";
      loadStrip(v).catch(() => {});
    })), 300);
  });
}

$("vdReconvertBtn").onclick = safe(async () => {
  await api(`/api/videos/${current}/convert`, { mode: $("vdRemode").value });
  toast("Converting again");
  await loadVideos();
});
$("vdRename").onclick = safe(() => rename(cur()));
$("vdDelete").onclick = safe(() => remove(cur()));

// ---- options (Settings for videos, kept on this page) ----
let optsShown = false;
function renderOptions() {
  if (optsShown || !lib.settings) return;
  optsShown = true;
  $("vdMaxMb").value = lib.settings.max_upload_mb;
  $("vdKeepDefault").checked = !!lib.settings.keep_original;
  $("vdKeep").checked = !!lib.settings.keep_original;
  $("vdFps").value = lib.settings.fps;
  $("vdTitleSource").value = lib.settings.title_source;
  $("vdShowTitles").checked = lib.settings.show_title !== false;
}
for (const id of ["vdMaxMb", "vdKeepDefault", "vdFps", "vdTitleSource"]) {
  $(id).addEventListener("change", safe(() => autosave("vdOptsSaved", async () => {
    const videos = { max_upload_mb: Math.max(1, +$("vdMaxMb").value || 1024), keep_original: $("vdKeepDefault").checked,
                     fps: Math.max(1, Math.min(30, Math.round(+$("vdFps").value) || 15)), title_source: $("vdTitleSource").value };
    await api("/api/settings", { videos });
    lib.settings = { ...lib.settings, ...videos };
    if (id === "vdKeepDefault") $("vdKeep").checked = videos.keep_original;
  })));
}

// ---- live: follow the board ----
let lastVid = null;
bus.addEventListener("state", () => {
  const r = board.runner;
  const vid = r?.video?.id || null;
  if (followNext && vid && vid !== current && lib.videos.some(v => v.id === vid)) { followNext = false; select(vid); }
  if (vid !== lastVid) {  // started, stopped or switched: the "playing" marks in the grid
    lastVid = vid;
    if (!$("tab-videos").hidden) $("vdGrid").replaceChildren(...lib.videos.map(card));
  }
  if (!$("tab-videos").hidden && current) renderTime();
  renderProgress(r);
});

// a thin progress bar under "what's on" (the strip and Now playing) while a video plays
function renderProgress(r) {
  document.querySelectorAll(".statusWhy").forEach(why => {
    let bar = why.parentElement.querySelector(":scope > .vidProgress");
    if (!bar) {
      bar = el("div", { class: "vidProgress", "aria-hidden": "true" }, el("span"));
      why.after(bar);
    }
    bar.hidden = !r?.video;
    if (r?.video) bar.firstChild.style.width = `${(100 * r.video.pos / (r.video.duration || 1)).toFixed(2)}%`;
  });
}

bus.addEventListener("tabShown", e => {
  if (e.detail === "videos") loadVideos().catch(err => toast(err.message, true));
  else if (fresh.size) fresh.clear();
});
