// Layers tab: build stacks of apps with blend modes. A saved stack is a "layer set" (the API calls it a composition).
// Edits save themselves into the set; when the set being edited is what's on the board, they show there right away too.
// Also builds the transitions gallery (shown on the Playlists page).
import { $, api, appOptions, ask, autosave, board, bus, catalog, compCache, confirmDelete, confirmDialog, drawingsCache, el, flushUndo, icon, iconButton, loadDrawings, nameFree, paramForm, safe, toast, undoable } from "./common.js";

// [value, plain name, what it does]
const BLENDS = [
  ["normal", "On top", "covers everything below inside its box"],
  ["add", "Add (light up)", "its lit dots light up on top; dark dots let what's below show through"],
  ["halo", "Halo", "lit dots go on top with a dark outline, so text stays readable over busy layers"],
  ["xor", "Invert where overlapping", "where it's lit, the dots below swap between lit and dark"],
  ["mask", "Mask", "layers below only show through where this one is lit"],
  ["cut", "Cut out", "where it's lit, the layers below go dark"],
];
const BLEND_HELP = Object.fromEntries(BLENDS.map(([v, , help]) => [v, help]));

let comps = {}, current = null, layers = [], open = new Set();
let fromBoard = false;  // the editor holds an unsaved stack loaded from the board
let applyTimer = null, saveTimer = null, seen = "";

export async function loadCompositions() {
  comps = (await api("/api/compositions")).compositions;
  compCache.names = Object.keys(comps);
  if (current && !comps[current]) current = null;
  if (!current && !fromBoard) select(Object.keys(comps)[0] || null);
  renderBar();
  bus.dispatchEvent(new Event("compositions"));
}

const clone = o => JSON.parse(JSON.stringify(o));

// Is what's in the editor the stack on the board? Then edits go there live.
function linked() {
  const r = board.runner;
  if (!r || r.mode !== "app" || r.notification || r.app !== "layers") return false;
  return current ? r.layer_set === current : fromBoard && !r.layer_set;
}

// Picking a saved set only opens it here; Show on board puts it on the board.
function select(name) {
  flushUndo();
  flushSave();
  current = name;
  fromBoard = false;
  layers = name ? clone(comps[name].layers) : [];
  open.clear();
  renderBar();
  render();
}

function renderBar() {
  // a stack loaded from the board that isn't a saved set yet gets its own entry, so the name shown is never wrong
  $("lyComp").replaceChildren(...(current ? [] : [el("option", { value: "", selected: true }, layers.length ? "Unsaved layers" : "No layer set")]),
    ...Object.keys(comps).map(n => el("option", { value: n, selected: n === current }, n)));
  const tr = $("lyTransition");
  if (!tr.options.length) tr.replaceChildren(...catalog.transitions.map(t => el("option", { value: t, selected: t === "dissolve" }, t)));
  if (!$("lyAddApp").options.length) $("lyAddApp").replaceChildren(...appOptions(null, ["layers"]));
  $("lyRename").disabled = $("lyDelete").disabled = !current;
  $("lySaveAs").textContent = current ? "Save a copy…" : "Save as layer set…";
  renderState();
}

function renderState() {
  const on = linked();
  $("lyState").textContent = on ? "On the board now: changes show there right away."
    : current ? "Not on the board. Changes save to this set; press Show on board to see it."
    : layers.length ? "Not saved. Use Save as layer set… to keep these layers." : "";
}

function changed() {
  if (current) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSave, 500);
  } else {
    renderState();
  }
  if (!linked()) return;
  clearTimeout(applyTimer);
  applyTimer = setTimeout(() => run(true), 250);
}

// write pending edits to the saved set now
function flushSave() {
  if (!saveTimer) return;
  clearTimeout(saveTimer);
  saveTimer = null;
  if (!current || !comps[current]) return;
  comps[current] = { layers: clone(layers) };
  safe(() => autosave("lySaved", saveComps))();
}

async function run(live = false) {
  busy = true;
  try {
    await api("/api/layers", {
      layers,
      name: current,
      transition: live ? "none" : $("lyTransition").value,
      transition_duration: +$("lyTransDur").value || 1,
    });
    if (!current) fromBoard = true;  // an unsaved stack you put on the board stays linked to it
    seen = boardKey(board.runner);
    renderState();
  } catch (e) { toast(e.message, true); }
  finally { busy = false; applyTimer = null; }
}

function num(label, value, onchange, attrs = {}) {
  const input = el("input", { type: "number", value, ...attrs });
  input.addEventListener("input", () => onchange(input.value === "" ? null : Number(input.value)));
  return el("label", {}, label, input);
}

function layerCard(layer, index) {
  const app = catalog.byId[layer.app];
  const title = app ? app.title : layer.app;
  const isSnapshot = layer.app === "scene" && layer.params?.frames;
  const isDrawing = layer.app === "drawing";
  const drawingName = isDrawing ? drawingsCache.list.find(d => d.id === layer.params?.drawing)?.name : null;
  const name = isSnapshot ? "Board snapshot" : isDrawing ? `Drawing: ${drawingName || "?"}` : title;
  const move = d => () => {
    const j = index + d;
    if (j < 0 || j >= layers.length) return;
    [layers[index], layers[j]] = [layers[j], layers[index]];
    render(); changed();
  };
  const blend = el("select", { "aria-label": `How ${name} combines with the layers below` },
    BLENDS.map(([v, l]) => el("option", { value: v, selected: (layer.blend || "normal") === v }, l)));
  blend.onchange = () => { layer.blend = blend.value; render(); changed(); };
  const visible = el("input", { type: "checkbox", checked: layer.visible !== false, title: "Show this layer", "aria-label": `Show ${name}` });
  visible.onchange = () => { layer.visible = visible.checked; render(); changed(); };
  const isOpen = open.has(index);
  const tip = layer.app === "music" && layer.params?.layout === "line"
    ? "Tip: for a solid strip, use On top with Height 9 (or 10 with the progress line)." : null;

  const card = el("div", { class: "layer" + (layer.visible === false ? " hiddenLayer" : "") },
    el("div", { class: "head" },
      visible,
      el("div", { class: "name" }, name,
        el("small", {}, BLEND_HELP[layer.blend || "normal"] + (index === 0 ? " · bottom layer" : ""))),
      el("label", { class: "blendPick" }, el("span", { class: "sr" }, "Blend"), blend),
      isDrawing ? el("button", { onclick: () => bus.dispatchEvent(new CustomEvent("drawTarget", { detail: layer.params.drawing })) }, icon("draw"), "Draw on it") : null,
      el("div", { class: "group" },
        iconButton("up", "Move up", move(1)),
        iconButton("down", "Move down", move(-1)),
        iconButton("settings", isOpen ? "Hide options" : "Layer options", () => { isOpen ? open.delete(index) : open.add(index); render(); }, isOpen ? "active" : ""),
        iconButton("copy", "Duplicate layer", () => { layers.splice(index + 1, 0, clone(layer)); render(); changed(); }),
        iconButton("x", "Remove layer", () => {
          const [gone] = layers.splice(index, 1);
          open.clear(); render(); changed();
          undoable(`Removed ${isDrawing ? "drawing layer" : title}`, { undo: () => { layers.splice(index, 0, gone); render(); changed(); } });
        }, "danger"))));

  if (isOpen) {
    const body = el("div", { class: "body" });
    if (app && !isSnapshot && app.params.some(p => p.type !== "hidden")) {
      const form = paramForm(app, layer.params || {});
      const update = () => { layer.params = form.values(); changed(); };
      form.el.addEventListener("input", update);
      form.el.addEventListener("change", update);
      body.append(form.el);
    }
    const W = board.W, H = board.H;
    const set = key => v => { if (v === null) delete layer[key]; else layer[key] = v; changed(); };
    const negative = el("input", { type: "checkbox", checked: !!layer.invert });
    negative.onchange = () => { layer.invert = negative.checked; changed(); };
    const density = el("input", { type: "range", min: 0, max: 100, value: Math.round((layer.density ?? 1) * 100) });
    const dLabel = el("span", {}, ` ${density.value}%`);
    density.oninput = () => { layer.density = density.value / 100; dLabel.textContent = ` ${density.value}%`; changed(); };
    if (tip) body.append(el("p", { class: "muted" }, tip));
    body.append(el("div", { class: "geo" },
      num("X", layer.x ?? 0, set("x"), { min: -W, max: W }),
      num("Y", layer.y ?? 0, set("y"), { min: -H, max: H }),
      num("Width", layer.w ?? W, set("w"), { min: 1, max: W * 4 }),
      num("Height", layer.h ?? H, set("h"), { min: 1, max: H * 4 }),
      num("Scroll sideways (dots/s)", layer.dx ?? 0, set("dx"), { step: 1 }),
      num("Scroll up/down (dots/s)", layer.dy ?? 0, set("dy"), { step: 1 }),
      num("Speed ×", layer.speed ?? 1, set("speed"), { min: 0.1, max: 10, step: 0.1 }),
      (layer.blend === "halo") ? num("Halo width", layer.halo ?? 1, set("halo"), { min: 0, max: 5 }) : null,
      el("label", {}, el("span", {}, "Dots kept", dLabel), density),
      el("label", { title: "Swap this layer's lit and dark dots before it's combined" }, "Negative", negative)));
    card.append(body);
  }
  return card;
}

function render() {
  const list = $("lyList");
  if (!layers.length) {
    list.replaceChildren(el("p", { class: "muted" }, "No layers yet. Pick an app below and press Add layer."));
    return;
  }
  // show the top layer first
  list.replaceChildren(...layers.map((l, i) => layerCard(l, i)).reverse());
}

// ---- layer sets ----

async function saveComps() {
  comps = (await api("/api/compositions", { compositions: comps })).compositions;
  compCache.names = Object.keys(comps);
  renderBar();
  bus.dispatchEvent(new Event("compositions"));
}

// Load the stack that's on the board into the editor (asks before dropping an unsaved one that isn't).
async function loadRunning({ quiet = false } = {}) {
  const j = await api("/api/layers/current");
  if (!j.layers) { if (!quiet) toast("There aren't any layers on the board right now", true); return false; }
  await loadDrawings();
  flushUndo();
  flushSave();
  current = j.name && comps[j.name] ? j.name : null;
  fromBoard = !current;
  layers = clone(j.layers);
  open.clear();
  renderBar();
  render();
  if (!quiet) toast("Showing the layers that are on the board" + (current ? "" : ". Use Save as layer set… to keep them."));
  return true;
}

// When the board runs a layer stack, the editor follows it: when you open the tab, and when it changes
// from somewhere else (Add as layer, a drawing layer...). Our own edits don't reload it.
const boardKey = r => r && r.mode === "app" && !r.notification && r.app === "layers" ? `${r.layer_set}|${r.layers_rev}` : "";
let busy = false;
async function follow() {
  const key = boardKey(board.runner);
  if ($("tab-layers").hidden || key === seen) return;
  seen = key;
  if (!key || busy || applyTimer || saveTimer) return renderState();
  await loadRunning({ quiet: true });
}
bus.addEventListener("state", () => follow().catch(() => {}));
bus.addEventListener("tabShown", e => { if (e.detail === "layers") { seen = null; follow().catch(() => {}); } });

$("lyComp").onchange = () => {
  if (!$("lyComp").value) return;
  select($("lyComp").value);
};
$("lyNew").onclick = safe(async () => {
  const name = await ask({ title: "New layer set", label: "Name", confirmText: "Create", validate: nameFree(Object.keys(comps)) });
  if (!name) return;
  flushSave();
  comps[name] = { layers: [] };
  await autosave("lySaved", saveComps);
  select(name);
});
$("lySaveAs").onclick = safe(async () => {
  const name = await ask({ title: current ? "Save a copy" : "Save as layer set", label: "Name", confirmText: "Save",
                           value: current ? current + " copy" : "My layers", validate: nameFree(Object.keys(comps)) });
  if (!name) return;
  flushSave();
  const wasLinked = linked();
  comps[name] = { layers: clone(layers) };
  current = name;
  fromBoard = false;
  await autosave("lySaved", saveComps);
  if (wasLinked) await run(true);  // the board now shows this set
  toast(`Saved "${name}"`);
});
$("lyRename").onclick = safe(async () => {
  if (!current) return;
  const name = await ask({ title: "Rename layer set", label: "Name", value: current, confirmText: "Rename", validate: nameFree(Object.keys(comps), current) });
  if (!name || name === current) return;
  flushSave();
  // the server also updates playlists, the schedule and automations that use the old name
  const j = await api("/api/compositions/rename", { old: current, new: name });
  comps = j.compositions; compCache.names = Object.keys(comps);
  current = name;
  renderBar();
  bus.dispatchEvent(new Event("compositions"));
  bus.dispatchEvent(new Event("refs"));
  toast(j.updated ? `Renamed. Updated ${j.updated} place${j.updated === 1 ? "" : "s"} that used it.` : "Renamed");
});
$("lyDelete").onclick = safe(async () => {
  if (!current || !await confirmDelete("composition", current, `the layer set "${current}"`)) return;
  clearTimeout(saveTimer); saveTimer = null;
  const j = await api("/api/compositions/delete", { name: current });
  comps = j.compositions; compCache.names = Object.keys(comps);
  bus.dispatchEvent(new Event("compositions"));
  bus.dispatchEvent(new Event("refs"));
  select(Object.keys(comps)[0] || null);
});
$("lyRun").onclick = safe(() => run(false));
$("lyLoadRunning").onclick = safe(async () => {
  if (fromBoard && !linked() && layers.length && !await confirmDialog({ title: "Replace these unsaved layers?", message: "They aren't saved as a layer set.", confirmText: "Replace" })) return;
  await loadRunning();
});
$("lyAdd").onclick = () => {
  const app = catalog.byId[$("lyAddApp").value];
  layers.push({ app: app.id, params: paramForm(app).values(), blend: layers.length ? "add" : "normal" });
  open.add(layers.length - 1);
  render(); changed();
};
async function addDrawingLayer(rows, name) {
  const j = await api("/api/drawings", { rows, name });
  await loadDrawings();
  layers.push({ app: "drawing", params: { drawing: j.id }, blend: layers.length ? "add" : "normal" });
  render(); changed();
  return j.id;
}
$("lyAddDrawing").onclick = safe(async () => {
  const id = await addDrawingLayer(null, null);
  if (!linked()) await run(true);
  bus.dispatchEvent(new CustomEvent("drawTarget", { detail: id }));
  toast("Drawing layer added. Draw on it in Draw.");
});
$("lyAddBoard").onclick = safe(() => addDrawingLayer("board", "Board snapshot"));
bus.addEventListener("drawings", () => render());

// ---- transitions gallery ----

const DEMOS = [
  { app: "clock", params: { date: true } },
  { app: "text", params: { text: "Flip!", style: "static", size: "huge" } },
  { app: "plasma", params: {} },
  { app: "analog_clock", params: {} },
];
let demo = 0;

export function renderGallery() {
  $("trGallery").replaceChildren(...catalog.transitions.filter(t => t !== "none").map(t =>
    el("button", { title: `Preview ${t} on the board (replaces what's showing)`, onclick: safe(async e => {
      document.querySelectorAll("#trGallery button").forEach(b => b.classList.toggle("active", b === e.currentTarget));
      demo = (demo + 1) % DEMOS.length;
      await api("/api/run", { ...DEMOS[demo], transition: t, transition_duration: +$("trDur").value || 1 });
    }) }, t)));
}
