// Slideshows (Image & video tab): upload images / GIFs / zips, order them, set timings, play.
import { $, api, board, bus, catalog, el, galleriesCache, icon, iconButton, renderDots, rowsToGrid, safe, toast } from "./common.js";

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
    : [el("option", { value: "" }, "No slideshows yet — press New")]));
  $("ssBody").hidden = !current;
  if (!$("ssTransition").options.length)
    $("ssTransition").replaceChildren(...catalog.transitions.map(t => el("option", { value: t }, t)));
}

async function loadGallery() {
  gallery = current ? await api(`/api/galleries/${current}`) : null;
  render();
}

const settingsKey = () => JSON.stringify([gallery.settings.mode, gallery.settings.dither, gallery.settings.threshold, gallery.settings.invert]);

function render() {
  if (!gallery) { $("ssItems").replaceChildren(); return; }
  const st = gallery.settings;
  $("ssImageSecs").value = st.image_seconds;
  $("ssGifLoops").value = st.gif_loops;
  $("ssGifMin").value = st.gif_min_seconds;
  $("ssTransition").value = st.transition;
  $("ssTransDur").value = st.transition_duration;
  $("ssShuffle").checked = !!st.shuffle;
  $("ssMode").value = st.mode;
  $("ssDither").value = String(!!st.dither);
  $("ssThreshold").value = st.threshold;
  $("ssThVal").textContent = st.threshold;
  $("ssInvert").checked = !!st.invert;
  const total = gallery.items.reduce((t, i) => t + (i.kind === "gif" ? (i.length_ms / 1000) * (i.loops || st.gif_loops) : (i.seconds || st.image_seconds)), 0);
  $("ssStatus").textContent = gallery.items.length
    ? `${gallery.items.length} item${gallery.items.length === 1 ? "" : "s"} · about ${Math.round(total)} s per cycle`
    : "No images yet.";
  $("ssItems").replaceChildren(...gallery.items.map((item, i) => itemCard(item, i)));
  loadThumbs();
}

function itemCard(item, i) {
  const c = el("canvas", { width: 84 * 3, height: 42 * 3, title: "Click to play this now" });
  c.dataset.file = item.file;
  c.onclick = safe(async () => {
    await api(`/api/galleries/${current}/play`, { file: item.file });
    toast(`Playing ${item.name}`);
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
  const move = d => () => {
    const j = i + d;
    if (j < 0 || j >= gallery.items.length) return;
    [gallery.items[i], gallery.items[j]] = [gallery.items[j], gallery.items[i]];
    render(); saveItems();
  };
  return el("div", { class: "ssItem" + (board.runner?.description?.includes(`: ${item.name} (`) ? " playing" : "") },
    c,
    el("div", { class: "name", title: item.name }, (isGif ? `GIF · ${item.frames} frames · ` : "") + item.name),
    el("div", { class: "ctl" },
      input, el("span", { class: "muted" }, isGif ? "plays" : "s"),
      el("button", { title: "Play this now", "aria-label": "Play this now", class: "playNow", onclick: safe(async () => {
        await api(`/api/galleries/${current}/play`, { file: item.file });
        toast(`Playing ${item.name}`);
      }) }, icon("play")),
      el("span", { class: "spacer" }),
      iconButton("left", "Play earlier", move(-1)),
      iconButton("right", "Play later", move(1)),
      iconButton("x", "Remove from slideshow", () => { gallery.items.splice(i, 1); render(); saveItems(); }, "danger")));
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

function saveItems() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(safe(async () => {
    gallery = await api(`/api/galleries/${current}`, {
      items: gallery.items.map(i => ({ file: i.file, seconds: i.seconds ?? null, loops: i.loops ?? null })),
    });
    const g = galleriesCache.list.find(x => x.id === current);
    if (g) { g.count = gallery.items.length; renderSelect(); }
    render();
  }), 300);
}

async function saveSettings(patch) {
  gallery = await api(`/api/galleries/${current}`, { settings: patch });
  render();
}

// ---- uploads ----

async function upload(files) {
  if (!current) {
    const j = await api("/api/galleries", { name: "My slideshow" });
    current = j.id;
    await loadGalleries();
  }
  let added = 0;
  const skipped = [];
  for (const [n, file] of [...files].entries()) {
    $("ssStatus").textContent = `Uploading ${file.name} (${n + 1}/${files.length})…`;
    try {
      const covers = $("ssSkipCovers").checked ? "skip" : "keep";
      const j = await api(`/api/galleries/${current}/upload?name=${encodeURIComponent(file.name)}&covers=${covers}`, file, { raw: true });
      added += j.added;
      skipped.push(...j.skipped);
    } catch (e) {
      skipped.push(`${file.name}: ${e.message}`);
    }
  }
  await loadGalleries();
  toast(`Added ${added} item${added === 1 ? "" : "s"}` + (skipped.length ? ` · skipped: ${skipped.slice(0, 3).join("; ")}${skipped.length > 3 ? "…" : ""}` : ""), added === 0);
}

const drop = $("ssDrop");
drop.onclick = () => $("ssFile").click();
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
  const name = prompt("Name for the slideshow?", `Slideshow ${galleriesCache.list.length + 1}`);
  if (!name) return;
  current = (await api("/api/galleries", { name })).id;
  await loadGalleries();
});
$("ssRename").onclick = safe(async () => {
  if (!current) return;
  const name = prompt("New name?", gallery.name);
  if (!name) return;
  await api(`/api/galleries/${current}`, { name });
  await loadGalleries();
});
$("ssDelete").onclick = safe(async () => {
  if (!current || !confirm(`Delete slideshow "${gallery.name}" and its ${gallery.items.length} images?`)) return;
  await api(`/api/galleries/${current}/delete`, {});
  current = null;
  await loadGalleries();
});
$("ssAddLayer").onclick = safe(async () => {
  if (!gallery?.items.length) return toast("Add some images first", true);
  const position = $("ssLayerPos").value;
  await api("/api/layers/add", { layer: { app: "slideshow", params: { gallery: current } }, position });
  toast(`Added "${gallery.name}" as a layer ${position === "top" ? "on top" : "underneath"}`);
});
$("ssRemoveCovers").onclick = safe(async () => {
  if (!current) return;
  const j = await api(`/api/galleries/${current}/remove_covers`, {});
  await loadGalleries();
  toast(j.removed ? `Removed ${j.removed} cover image${j.removed === 1 ? "" : "s"}` : "No cover images found");
});
$("ssPlay").onclick = safe(async () => {
  if (!gallery?.items.length) return toast("Add some images first", true);
  await api(`/api/galleries/${current}/play`, {});
  toast(`Playing "${gallery.name}"`);
});

const num = id => Number($(id).value);
$("ssImageSecs").onchange = safe(() => saveSettings({ image_seconds: Math.max(1, num("ssImageSecs") || 10) }));
$("ssGifLoops").onchange = safe(() => saveSettings({ gif_loops: Math.max(1, num("ssGifLoops") || 1) }));
$("ssGifMin").onchange = safe(() => saveSettings({ gif_min_seconds: Math.max(0, num("ssGifMin") || 0) }));
$("ssTransition").onchange = safe(() => saveSettings({ transition: $("ssTransition").value }));
$("ssTransDur").onchange = safe(() => saveSettings({ transition_duration: Math.max(0.1, num("ssTransDur") || 1) }));
$("ssShuffle").onchange = safe(() => saveSettings({ shuffle: $("ssShuffle").checked }));
$("ssMode").onchange = safe(() => saveSettings({ mode: $("ssMode").value }));
$("ssDither").onchange = safe(() => saveSettings({ dither: $("ssDither").value === "true" }));
$("ssThreshold").oninput = () => ($("ssThVal").textContent = $("ssThreshold").value);
$("ssThreshold").onchange = safe(() => saveSettings({ threshold: num("ssThreshold") }));
$("ssInvert").onchange = safe(() => saveSettings({ invert: $("ssInvert").checked }));

// highlight the item that's on the board
let lastDesc = null;
bus.addEventListener("state", () => {
  const d = board.runner?.description;
  if (d === lastDesc || !gallery) return;
  lastDesc = d;
  document.querySelectorAll(".ssItem").forEach((card, i) =>
    card.classList.toggle("playing", !!d && d.startsWith(gallery.name + ": ") && d.includes(`: ${gallery.items[i]?.name} (`)));
});
