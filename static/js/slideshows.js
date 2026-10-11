// Slideshows page: upload images / GIFs / zips, order them, set timings, play.
import { $, api, ask, autosave, board, bus, catalog, confirmDelete, el, flushUndo, galleriesCache, icon, iconButton, renderDots, rowsToGrid, safe, toast, undoable, uploadFile } from "./common.js";
import { reactTable, setStatus } from "./musicreact.js";
import { addToPlaylistButton } from "./addto.js";

let current = null, gallery = null, saveTimer = null;
const thumbs = new Map();  // `${gid}/${file}/${settingsKey}` -> rows

export async function loadGalleries() {
  galleriesCache.list = (await api("/api/galleries")).galleries;
  if (!current || !galleriesCache.list.some(g => g.id === current)) current = galleriesCache.list[0]?.id || null;
  renderSelect();
  await loadGallery();
  bus.dispatchEvent(new Event("galleries"));
}

function renderSelect() {
  $("ssSelect").replaceChildren(...(galleriesCache.list.length
    ? galleriesCache.list.map(g => el("option", { value: g.id, selected: g.id === current }, `${g.name} (${g.count})`))
    : [el("option", { value: "" }, "No slideshows yet")]));
  for (const id of ["ssRename", "ssDelete"]) $(id).disabled = !current;
  if (!$("ssTransition").options.length)
    $("ssTransition").replaceChildren(...catalog.transitions.map(t => el("option", { value: t }, t)));
}

async function loadGallery() {
  flushUndo();  // finish a pending item removal before swapping what's shown
  gallery = current ? await api(`/api/galleries/${current}`) : null;
  render();
}

const settingsKey = () => JSON.stringify([gallery.settings.mode, gallery.settings.dither, gallery.settings.threshold, gallery.settings.invert]);

const MODE_HELP = {
  stretch: "Fills the board; the picture is squashed or stretched to fit.",
  crop: "Fills the board; edges that don't fit are cut off.",
  fit: "Shows the whole picture, with black bars where it doesn't fill the board.",
};
const DITHER_HELP = {
  false: "Each dot is lit or dark by brightness alone: crisp, good for logos and drawings.",
  true: "Mixes lit and dark dots to fake shades of grey: better for photos.",
};

function render() {
  const empty = !gallery?.items.length;
  $("ssPlay").disabled = $("ssAddLayer").disabled = empty;
  $("ssPlay").title = $("ssAddLayer").title = empty ? "Add some pictures first" : "";
  $("ssOptions").hidden = !gallery;
  if (!gallery) {
    $("ssItems").replaceChildren();
    $("ssStatus").textContent = "Drop pictures above to start a slideshow.";
    renderNow();
    return;
  }
  const st = gallery.settings;
  $("ssImageSecs").value = st.image_seconds;
  $("ssGifLoops").value = st.gif_loops;
  $("ssGifMin").value = st.gif_min_seconds;
  $("ssGifSpeed").value = String(st.gif_speed || 1);
  $("ssTransition").value = st.transition;
  $("ssTransDur").value = st.transition_duration;
  $("ssShuffle").checked = !!st.shuffle;
  $("ssMode").value = st.mode;
  $("ssDither").value = String(!!st.dither);
  $("ssThreshold").value = st.threshold;
  $("ssThVal").textContent = st.threshold;
  $("ssInvert").checked = !!st.invert;
  $("ssModeHelp").textContent = MODE_HELP[st.mode] || "";
  $("ssDitherHelp").textContent = DITHER_HELP[!!st.dither];
  renderFx(st);
  renderReact(st);
  const total = gallery.items.reduce((t, i) => t + (i.kind === "gif" ? (i.length_ms / 1000) / (i.speed || st.gif_speed || 1) * (i.loops || st.gif_loops) : (i.seconds || st.image_seconds)), 0);
  if (!busy) $("ssStatus").textContent = gallery.items.length
    ? `${gallery.items.length} item${gallery.items.length === 1 ? "" : "s"} · about ${Math.round(total)} s per cycle`
    : "No images yet. Drop some above.";
  $("ssItems").replaceChildren(...gallery.items.map((item, i) => itemCard(item, i)));
  loadThumbs();
  renderNow();
}

function itemCard(item, i) {
  const c = el("canvas", { width: 84 * 3, height: 42 * 3, title: "Click to show this picture now" });
  c.dataset.file = item.file;
  c.onclick = safe(async () => {
    await api(`/api/galleries/${current}/play`, { file: item.file });
    toast(`Showing ${item.name}`);
  });
  const isGif = item.kind === "gif";
  const input = el("input", {
    type: "number", min: isGif ? 1 : 1, step: 1,
    placeholder: isGif ? String(gallery.settings.gif_loops) : String(gallery.settings.image_seconds),
    value: isGif ? (item.loops ?? "") : (item.seconds ?? ""),
    title: isGif ? "times to play (blank = default)" : "seconds on screen (blank = default)",
  });
  input.onchange = () => {
    const v = input.value === "" ? null : Number(input.value);
    if (isGif) item.loops = v; else item.seconds = v;
    saveItems();
  };
  // GIFs: their own speed, blank = the slideshow's
  const speed = isGif ? el("select", { title: "Speed for this GIF", "aria-label": `Speed for ${item.name}` },
    el("option", { value: "" }, `${gallery.settings.gif_speed || 1}× (default)`),
    ...[0.5, 0.75, 1, 1.5, 2, 3, 4].map(v => el("option", { value: String(v) }, `${v}×`))) : null;
  if (speed) {
    speed.value = item.speed ? String(item.speed) : "";
    speed.onchange = () => { item.speed = speed.value === "" ? null : Number(speed.value); saveItems(); };
  }
  const move = d => () => {
    const j = i + d;
    if (j < 0 || j >= gallery.items.length) return;
    [gallery.items[i], gallery.items[j]] = [gallery.items[j], gallery.items[i]];
    render(); saveItems();
  };
  const playing = board.runner?.slideshow?.gallery === current && board.runner.slideshow.file === item.file;
  return el("div", { class: "ssItem" + (playing ? " playing" : ""), "data-file": item.file, "aria-current": playing ? "true" : null },
    c,
    el("div", { class: "name", title: item.name }, (isGif ? `GIF · ${item.frames} frames · ` : "") + item.name),
    el("div", { class: "ctl" },
      input, el("span", { class: "muted" }, isGif ? "plays" : "s"), speed,
      el("button", { title: "Show this picture now", "aria-label": `Show ${item.name} now`, class: "playNow", onclick: safe(async () => {
        await api(`/api/galleries/${current}/play`, { file: item.file });
        toast(`Showing ${item.name}`);
      }) }, icon("play")),
      el("span", { class: "spacer" }),
      iconButton("left", "Play earlier", move(-1)),
      iconButton("right", "Play later", move(1)),
      iconButton("x", "Remove from slideshow", () => removeItem(i), "danger")));
}

// The item leaves the list now; its file is only deleted on the server once Undo is no longer offered.
function removeItem(i) {
  flushUndo();
  const g = gallery, [gone] = g.items.splice(i, 1);
  render();
  undoable(`Removed ${gone.name}`, {
    undo: () => { g.items.splice(i, 0, gone); if (gallery === g) render(); },
    commit: () => saveItems(g, true),
  });
}

async function loadThumbs() {
  const key = settingsKey(), gid = current;
  for (const canvas of $("ssItems").querySelectorAll("canvas")) {
    const file = canvas.dataset.file, k = `${gid}/${file}/${key}`;
    let rows = thumbs.get(k);
    if (!rows) {
      try { rows = (await api(`/api/galleries/${gid}/thumb?file=${encodeURIComponent(file)}`)).rows; }
      catch { rows = null; }
      if (gid !== current || key !== settingsKey()) return;  // moved on meanwhile
      thumbs.set(k, rows);
    }
    if (rows) renderDots(canvas.getContext("2d"), rowsToGrid(rows), 3, { seams: false });
  }
}

function saveItems(g = gallery, now = false) {
  if (!now) flushUndo();  // a pending removal goes out with this save
  clearTimeout(saveTimer);
  const go = safe(async () => {
    const saved = await autosave("ssSaved", () => api(`/api/galleries/${g.id}`, {
      items: g.items.map(i => ({ file: i.file, seconds: i.seconds ?? null, loops: i.loops ?? null, speed: i.speed ?? null })),
    }));
    const c = galleriesCache.list.find(x => x.id === g.id);
    if (c) { c.count = saved.items.length; renderSelect(); }
    if (gallery?.id === g.id) { gallery = saved; render(); }
  });
  if (now) go(); else saveTimer = setTimeout(go, 300);
}

async function saveSettings(patch) {
  gallery = await autosave("ssSaved", () => api(`/api/galleries/${current}`, { settings: patch }));
  render();
}

// ---- uploads ----

let busy = false;
async function upload(files) {
  if (!current) {
    const j = await api("/api/galleries", { name: "My slideshow" });
    current = j.id;
    await loadGalleries();
  }
  files = [...files];
  const gid = current, total = files.reduce((t, f) => t + f.size, 0) || 1;
  let added = 0, sent = 0;
  const skipped = [];
  const bar = $("ssProgress");
  busy = true;
  bar.hidden = false;
  try {
    for (const [n, file] of files.entries()) {
      const show = loaded => {
        const pct = Math.round(100 * (sent + loaded) / total);
        $("ssStatus").textContent = `Uploading ${file.name} (${n + 1} of ${files.length}) · ${pct}%` +
          (loaded >= file.size && /\.zip$/i.test(file.name) ? " · unpacking…" : "");
        bar.firstChild.style.width = `${pct}%`;
        bar.setAttribute("aria-valuenow", pct);
      };
      show(0);
      try {
        const covers = $("ssSkipCovers").checked ? "skip" : "keep";
        const j = await uploadFile(`/api/galleries/${gid}/upload?name=${encodeURIComponent(file.name)}&covers=${covers}`, file, show);
        added += j.added;
        skipped.push(...j.skipped);
      } catch (e) {
        skipped.push(`${file.name}: ${e.message}`);
      }
      sent += file.size;
    }
  } finally {
    busy = false;
    bar.hidden = true;
  }
  await loadGalleries();
  const msg = `Added ${added} item${added === 1 ? "" : "s"}` + (skipped.length ? ` · skipped: ${skipped.slice(0, 3).join("; ")}${skipped.length > 3 ? "…" : ""}` : "");
  toast(msg, added === 0);
  if (skipped.length && added) $("ssStatus").textContent += ` · skipped ${skipped.length}: ${skipped.slice(0, 3).join("; ")}`;
  return added;
}

// From Import: a new slideshow from these files, shown open on the Slideshows page while they upload.
export async function importToSlideshow(files, name) {
  const { id } = await api("/api/galleries", { name });
  current = id;
  await loadGalleries();
  const mark = () => { $("ssSelect").classList.add("justAdded"); $("ssItems").classList.add("justAdded"); };
  mark();
  $("ssSelect").scrollIntoView({ block: "center", behavior: "smooth" });
  await upload(files);
  mark();
  $("ssItems").scrollIntoView({ block: "nearest", behavior: "smooth" });
}
bus.addEventListener("tabShown", e => {
  if (e.detail !== "slideshows") { $("ssSelect").classList.remove("justAdded"); $("ssItems").classList.remove("justAdded"); }
});

const drop = $("ssDrop");
drop.onclick = () => $("ssFile").click();
drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("ssFile").click(); } };
$("ssFile").onchange = safe(async e => { await upload(e.target.files); e.target.value = ""; });
drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", safe(async e => {
  e.preventDefault(); drop.classList.remove("over");
  await upload(e.dataTransfer.files);
}));

// ---- slideshow management ----

$("ssSelect").onchange = safe(async () => { current = $("ssSelect").value; await loadGallery(); });
$("ssNew").onclick = safe(async () => {
  const name = await ask({ title: "New slideshow", label: "Name", value: `Slideshow ${galleriesCache.list.length + 1}`, confirmText: "Create" });
  if (!name) return;
  current = (await api("/api/galleries", { name })).id;
  await loadGalleries();
});
$("ssRename").onclick = safe(async () => {
  if (!current) return;
  const name = await ask({ title: "Rename slideshow", label: "Name", value: gallery.name, confirmText: "Rename" });
  if (!name || name === gallery.name) return;
  await api(`/api/galleries/${current}`, { name });
  await loadGalleries();
});
$("ssDelete").onclick = safe(async () => {
  if (!current || !await confirmDelete("gallery", current, `the slideshow "${gallery.name}" and its ${gallery.items.length} images`)) return;
  await api(`/api/galleries/${current}/delete`, {});
  current = null;
  await loadGalleries();
  bus.dispatchEvent(new Event("refs"));
});
$("ssAddLayer").onclick = safe(async () => {
  if (!gallery?.items.length) return toast("Add some images first", true);
  const position = $("ssLayerPos").value;
  await api("/api/layers/add", { layer: { app: "slideshow", params: { gallery: current } }, position });
  toast(`Added "${gallery.name}" as a layer ${position === "top" ? "on top" : "underneath"}`);
});
$("ssAddLayer").after(addToPlaylistButton(() => {
  if (!gallery?.items.length) { toast("Add some images first", true); return null; }
  return { app: "slideshow", params: { gallery: current } };
}, { name: () => gallery?.name || "", duration: () => 120 }));
$("ssRemoveCovers").onclick = safe(async () => {
  if (!current) return;
  const j = await api(`/api/galleries/${current}/remove_covers`, {});
  await loadGalleries();
  toast(j.removed ? `Removed ${j.removed} cover image${j.removed === 1 ? "" : "s"}` : "No cover images found");
});
$("ssPlay").onclick = safe(async () => {
  if (!gallery?.items.length) return toast("Add some images first", true);
  await api(`/api/galleries/${current}/play`, {});
  toast(`Showing "${gallery.name}"`);
});

// ---- while this slideshow is on the board: where it is, Previous / Pause / Next ----
function renderNow() {
  const r = board.runner, sl = r?.slideshow;
  const mine = !!gallery && sl?.gallery === current;
  $("ssNow").hidden = !mine;
  document.querySelectorAll(".ssItem").forEach(card => {
    const on = mine && card.dataset.file === sl.file;
    card.classList.toggle("playing", on);
    if (on) card.setAttribute("aria-current", "true"); else card.removeAttribute("aria-current");
  });
  if (!mine) return;
  const n = gallery.items.findIndex(i => i.file === sl.file);
  const yours = r.mode === "app";
  $("ssNowText").textContent = `On the board: ${gallery.items[n]?.name || "…"}` + (n >= 0 ? ` (${n + 1} of ${gallery.items.length})` : "")
    + (r.paused ? " · paused" : "") + (yours ? "" : " · playing from a playlist or the schedule");
  for (const id of ["ssPrev", "ssNext", "ssPause"]) $(id).disabled = !yours;
  $("ssPause").setAttribute("aria-pressed", String(!!r.paused));
  $("ssPause").querySelector("span").textContent = r.paused ? "Resume" : "Pause";
  $("ssPause").querySelector("use").setAttribute("href", r.paused ? "#i-play" : "#i-pause");
}
$("ssPrev").onclick = safe(() => api("/api/prev", {}));
$("ssNext").onclick = safe(() => api("/api/next", {}));
$("ssPause").onclick = safe(() => api("/api/pause", { paused: !board.runner?.paused }));

const num = id => Number($(id).value);
$("ssImageSecs").onchange = safe(() => saveSettings({ image_seconds: Math.max(1, num("ssImageSecs") || 10) }));
$("ssGifLoops").onchange = safe(() => saveSettings({ gif_loops: Math.max(1, num("ssGifLoops") || 1) }));
$("ssGifMin").onchange = safe(() => saveSettings({ gif_min_seconds: Math.max(0, num("ssGifMin") || 0) }));
$("ssGifSpeed").onchange = safe(() => saveSettings({ gif_speed: Number($("ssGifSpeed").value) || 1 }));
$("ssTransition").onchange = safe(() => saveSettings({ transition: $("ssTransition").value }));
$("ssTransDur").onchange = safe(() => saveSettings({ transition_duration: Math.max(0.1, num("ssTransDur") || 1) }));
$("ssShuffle").onchange = safe(() => saveSettings({ shuffle: $("ssShuffle").checked }));
$("ssMode").onchange = safe(() => saveSettings({ mode: $("ssMode").value }));
$("ssDither").onchange = safe(() => saveSettings({ dither: $("ssDither").value === "true" }));
$("ssThreshold").oninput = () => ($("ssThVal").textContent = $("ssThreshold").value);
$("ssThreshold").onchange = safe(() => saveSettings({ threshold: num("ssThreshold") }));
$("ssInvert").onchange = safe(() => saveSettings({ invert: $("ssInvert").checked }));

// ---- random effects ----
const FX = [["invert", "Invert"], ["sweep", "Invert sweep"], ["flash", "Flash"], ["glitch", "Glitch"], ["mirror", "Mirror"],
  ["kaleidoscope", "Kaleidoscope"], ["wave", "Wave"], ["scanlines", "Scanlines"], ["pixelate", "Pixelate"], ["outline", "Outline"],
  ["static", "Static"], ["life", "Game of Life"], ["melt", "Melt"]];
const pct = v => (+v ? `${v}%` : "Off");

function renderFx(st) {
  const chosen = new Set(st.fx_effects ?? FX.map(f => f[0]));
  $("ssFxChance").value = st.fx_chance ?? 0;
  $("ssFxChanceVal").textContent = pct(st.fx_chance ?? 0);
  $("ssFxEffects").replaceChildren(...FX.map(([id, label]) => el("button", {
    "aria-pressed": String(chosen.has(id)), class: chosen.has(id) ? "active" : "",
    onclick: safe(async () => {
      const next = FX.map(f => f[0]).filter(f => (f === id) !== chosen.has(f));
      if (!next.length) return toast("Keep at least one effect selected", true);
      await saveSettings({ fx_effects: next });
    }),
  }, label)));
  const mode = st.fx_mode || "burst";
  document.querySelectorAll("#ssFxMode [data-fx-mode]").forEach(b => {
    b.classList.toggle("active", b.dataset.fxMode === mode);
    b.setAttribute("aria-pressed", String(b.dataset.fxMode === mode));
  });
  $("ssFxSeconds").value = st.fx_seconds ?? 3;
  $("ssFxSeconds").disabled = mode !== "burst";
  $("ssFxSecondsNote").textContent = mode === "burst" ? "seconds, at a random moment in the item" : "Not used: the effect lasts the whole item.";
  $("ssFxStrength").value = st.fx_strength ?? 50;
  $("ssFxStrengthVal").textContent = `${st.fx_strength ?? 50}%`;
}

$("ssFxChance").oninput = () => ($("ssFxChanceVal").textContent = pct($("ssFxChance").value));
$("ssFxChance").onchange = safe(() => saveSettings({ fx_chance: Math.max(0, Math.min(100, num("ssFxChance"))) }));
$("ssFxStrength").oninput = () => ($("ssFxStrengthVal").textContent = `${$("ssFxStrength").value}%`);
$("ssFxStrength").onchange = safe(() => saveSettings({ fx_strength: Math.max(0, Math.min(100, num("ssFxStrength"))) }));
$("ssFxSeconds").onchange = safe(() => saveSettings({ fx_seconds: Math.max(0.3, Math.min(30, num("ssFxSeconds") || 3)) }));
document.querySelectorAll("#ssFxMode [data-fx-mode]").forEach(b => { b.onclick = safe(() => saveSettings({ fx_mode: b.dataset.fxMode })); });

// ---- React to music (the table is shared with the generative apps: musicreact.js) ----
let reactFor = null;  // gallery id the rows were built for
const reactMap = reactTable({ box: $("ssReactRows"), onChange: rows => saveSettings({ react_map: rows }) });

function renderReact(st) {
  $("ssReact").checked = !!st.react_enabled;
  $("ssReactBody").hidden = !st.react_enabled;
  $("ssReactSpeed").checked = !!st.react_speed;
  $("ssReactSkip").checked = !!st.react_skip;
  $("ssReactFallback").value = st.react_fallback || "none";
  reactMap.fill(st.react_map, reactFor !== current);
  reactFor = current;
  pollLevels(true);
}

$("ssReact").onchange = safe(() => saveSettings({ react_enabled: $("ssReact").checked }));
$("ssReactSpeed").onchange = safe(() => saveSettings({ react_speed: $("ssReactSpeed").checked }));
$("ssReactSkip").onchange = safe(() => saveSettings({ react_skip: $("ssReactSkip").checked }));
$("ssReactFallback").onchange = safe(() => saveSettings({ react_fallback: $("ssReactFallback").value }));

// Status line + live meters: polled ~5 times a second while the section is on screen (once every few
// seconds when reacting is off, just for the status line); nothing while the page is hidden.
let levelsTimer = null, levelsBusy = false, reactVisible = false;
async function fetchLevels() {
  if (levelsBusy || !gallery) return;
  levelsBusy = true;
  try {
    const fb = gallery.settings.react_fallback === "beat" ? "?fallback=beat" : "";
    const j = await api(`/api/audio/levels${fb}`);
    setStatus($("ssReactStatus"), j);
    reactMap.setMeters(j);
  } catch { /* the next poll tries again */ } finally { levelsBusy = false; }
}
function pollLevels(now = false) {
  clearInterval(levelsTimer);
  levelsTimer = null;
  if (!reactVisible || document.hidden || !gallery) return;
  if (now) fetchLevels();
  levelsTimer = setInterval(fetchLevels, gallery.settings.react_enabled ? 200 : 3000);
}
new IntersectionObserver(es => { reactVisible = es.some(e => e.isIntersecting); pollLevels(true); })
  .observe($("ssReactStatus"));
document.addEventListener("visibilitychange", () => pollLevels(true));

// highlight the picture that's on the board
let lastNow = "";
bus.addEventListener("state", () => {
  const r = board.runner, key = JSON.stringify([r?.slideshow, r?.paused, r?.mode]);
  if (key === lastNow) return;
  lastNow = key;
  renderNow();
});
