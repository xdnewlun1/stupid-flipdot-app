// Layers tab: build stacks of apps with blend modes, edit them live, save compositions.
// Also hosts the transitions gallery.
import { $, api, board, bus, catalog, compCache, drawingsCache, el, icon, iconButton, loadDrawings, paramForm, safe, toast } from "./common.js";

const BLENDS = [
  ["normal", "Normal"], ["add", "Add"], ["halo", "Halo"], ["xor", "Invert"], ["mask", "Mask"], ["cut", "Cut out"],
];
const BLEND_HELP = {
  halo: "its white dots go on top with a black outline, so text stays readable over busy layers",
  normal: "covers what's below inside its box",
  add: "its white dots are drawn on top",
  xor: "flips the dots below wherever it's white",
  mask: "layers below only show where this is white",
  cut: "erases layers below wherever this is white",
};

let comps = {}, current = null, layers = [], dirty = false, open = new Set();
let applyTimer = null;

export async function loadCompositions() {
  comps = (await api("/api/compositions")).compositions;
  compCache.names = Object.keys(comps);
  if (!current || !comps[current]) select(Object.keys(comps)[0] || null, false);
  renderBar();
  bus.dispatchEvent(new Event("compositions"));
}

const clone = o => JSON.parse(JSON.stringify(o));

function select(name, apply = true) {
  current = name;
  layers = name ? clone(comps[name].layers) : [];
  open.clear();
  setDirty(false);
  render();
  if (apply && $("lyLive").checked && name) run(true);
}

function setDirty(d) {
  dirty = d;
  $("lyDirty").textContent = d ? "unsaved changes" : "";
}

function renderBar() {
  $("lyComp").replaceChildren(...Object.keys(comps).map(n => el("option", { value: n, selected: n === current }, n)));
  const tr = $("lyTransition");
  if (!tr.options.length) tr.replaceChildren(...catalog.transitions.map(t => el("option", { value: t, selected: t === "dissolve" }, t)));
  $("lyAddApp").replaceChildren(...catalog.apps.filter(a => a.id !== "layers")
    .map(a => el("option", { value: a.id }, `${a.category}: ${a.title}`)));
}

function changed() {
  setDirty(true);
  if (!$("lyLive").checked) return;
  clearTimeout(applyTimer);
  applyTimer = setTimeout(() => run(true), 250);
}

async function run(live = false) {
  try {
    await api("/api/layers", {
      layers,
      transition: live ? "none" : $("lyTransition").value,
      transition_duration: +$("lyTransDur").value || 1,
    });
  } catch (e) { toast(e.message, true); }
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
  const move = d => () => {
    const j = index + d;
    if (j < 0 || j >= layers.length) return;
    [layers[index], layers[j]] = [layers[j], layers[index]];
    render(); changed();
  };
  const blend = el("select", {}, BLENDS.map(([v, l]) => el("option", { value: v, selected: (layer.blend || "normal") === v }, l)));
  blend.onchange = () => { layer.blend = blend.value; render(); changed(); };
  const visible = el("input", { type: "checkbox", checked: layer.visible !== false, title: "Show this layer" });
  visible.onchange = () => { layer.visible = visible.checked; render(); changed(); };
  const isOpen = open.has(index);
  const tip = layer.app === "music" && layer.params?.layout === "line"
    ? "Tip: for a solid strip, use Normal blend with Height 9 (or 10 with the progress line)." : null;

  const card = el("div", { class: "layer" + (layer.visible === false ? " hiddenLayer" : "") },
    el("div", { class: "head" },
      visible,
      el("div", { class: "name" }, isSnapshot ? "Board snapshot" : isDrawing ? `Drawing: ${drawingName || "?"}` : title,
        el("small", {}, BLEND_HELP[layer.blend || "normal"] + (index === 0 ? " · bottom layer" : ""))),
      blend,
      isDrawing ? el("button", { onclick: () => bus.dispatchEvent(new CustomEvent("drawTarget", { detail: layer.params.drawing })) }, icon("draw"), "Draw on it") : null,
      el("div", { class: "group" },
        iconButton("up", "Move up", move(1)),
        iconButton("down", "Move down", move(-1)),
        iconButton(isOpen ? "up" : "settings", isOpen ? "Hide options" : "Layer options", () => { isOpen ? open.delete(index) : open.add(index); render(); }),
        iconButton("copy", "Duplicate layer", () => { layers.splice(index + 1, 0, clone(layer)); render(); changed(); }),
        iconButton("x", "Remove layer", () => { layers.splice(index, 1); open.clear(); render(); changed(); }, "danger"))));

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
    const invert = el("input", { type: "checkbox", checked: !!layer.invert });
    invert.onchange = () => { layer.invert = invert.checked; changed(); };
    const density = el("input", { type: "range", min: 0, max: 100, value: Math.round((layer.density ?? 1) * 100) });
    const dLabel = el("span", {}, ` ${density.value}%`);
    density.oninput = () => { layer.density = density.value / 100; dLabel.textContent = ` ${density.value}%`; changed(); };
    if (tip) body.append(el("p", { class: "muted" }, tip));
    body.append(el("div", { class: "geo" },
      num("X", layer.x ?? 0, set("x"), { min: -W, max: W }),
      num("Y", layer.y ?? 0, set("y"), { min: -H, max: H }),
      num("Width", layer.w ?? W, set("w"), { min: 1, max: W * 4 }),
      num("Height", layer.h ?? H, set("h"), { min: 1, max: H * 4 }),
      num("Scroll X (dots/s)", layer.dx ?? 0, set("dx"), { step: 1 }),
      num("Scroll Y (dots/s)", layer.dy ?? 0, set("dy"), { step: 1 }),
      num("Speed ×", layer.speed ?? 1, set("speed"), { min: 0.1, max: 10, step: 0.1 }),
      (layer.blend === "halo") ? num("Halo width", layer.halo ?? 1, set("halo"), { min: 0, max: 5 }) : null,
      el("label", {}, el("span", {}, "Density", dLabel), density),
      el("label", {}, "Invert layer", invert)));
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

// ---- composition management ----

async function saveComps() {
  comps = (await api("/api/compositions", { compositions: comps })).compositions;
  compCache.names = Object.keys(comps);
  renderBar();
  bus.dispatchEvent(new Event("compositions"));
}

$("lyComp").onchange = () => {
  if (dirty && !confirm("Discard unsaved changes?")) { $("lyComp").value = current; return; }
  select($("lyComp").value);
};
$("lyNew").onclick = safe(async () => {
  const name = prompt("Name for the new composition?");
  if (!name) return;
  if (comps[name]) return toast("That name is taken", true);
  comps[name] = { layers: [] };
  await saveComps();
  select(name, false);
});
$("lySave").onclick = safe(async () => {
  if (!current) return $("lySaveAs").click();
  comps[current] = { layers: clone(layers) };
  await saveComps();
  setDirty(false);
  toast(`Saved "${current}"`);
});
$("lySaveAs").onclick = safe(async () => {
  const name = prompt("Save as?", current ? current + " copy" : "My layers");
  if (!name) return;
  comps[name] = { layers: clone(layers) };
  current = name;
  await saveComps();
  setDirty(false);
  toast(`Saved "${name}"`);
});
$("lyRename").onclick = safe(async () => {
  if (!current) return;
  const name = prompt("New name?", current);
  if (!name || name === current) return;
  comps[name] = comps[current];
  delete comps[current];
  current = name;
  await saveComps();
});
$("lyDelete").onclick = safe(async () => {
  if (!current || !confirm(`Delete "${current}"?`)) return;
  delete comps[current];
  await saveComps();
  select(Object.keys(comps)[0] || null, false);
});
$("lyRun").onclick = () => run(false);
$("lyLoadRunning").onclick = safe(async () => {
  const j = await api("/api/layers/current");
  if (!j.layers) return toast("There isn't a layer composition on the board right now", true);
  await loadDrawings();
  if (dirty && !confirm("Discard unsaved changes?")) return;
  current = j.name && comps[j.name] ? j.name : null;
  layers = clone(j.layers);
  open.clear();
  renderBar();
  render();
  setDirty(!current);
  toast("Loaded the layers that are on the board" + (current ? "" : ". Use Save as… to keep them."));
});
$("lyAdd").onclick = () => {
  const app = catalog.byId[$("lyAddApp").value];
  layers.push({ app: app.id, params: paramForm(app).values(), blend: layers.length ? "xor" : "normal" });
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
  if (!$("lyLive").checked) await run(true);
  bus.dispatchEvent(new CustomEvent("drawTarget", { detail: id }));
  toast("Drawing layer added. Draw on it in the Draw tab.");
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
    el("button", { onclick: safe(async e => {
      document.querySelectorAll("#trGallery button").forEach(b => b.classList.toggle("active", b === e.currentTarget));
      demo = (demo + 1) % DEMOS.length;
      await api("/api/run", { ...DEMOS[demo], transition: t, transition_duration: +$("trDur").value || 1 });
    }) }, t)));
}
