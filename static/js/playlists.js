// Playlists page: items in order or shuffled, "only show if" conditions, per-item transitions, and random
// effects / React to music on top of every item (the same effects as slideshows: apps/slidefx.py).
// The Schedule page is static/js/schedule.js.
import { $, api, appOptions, ask, autosave, bus, catalog, confirmDelete, describeItem, el, flushUndo, iconButton, nameFree, paramForm, safe, toast, undoable } from "./common.js";
import { reactTable, setStatus } from "./musicreact.js";

let playlists = {}, current = null, editing = null;
let draft = null;      // an item being added: it joins the playlist only on "Add to playlist"
let warnings = {};     // playlist -> { item index: "needs setup" message }
let extras = { conditions: [], effects: [], react_defaults: [], motion: {} };

export const playlistNames = () => Object.keys(playlists);

export async function loadPlaylists() {
  const j = await api("/api/playlists");
  playlists = j.playlists; warnings = j.warnings || {};
  extras = { conditions: j.conditions || [], effects: j.effects || [], react_defaults: j.react_defaults || [], motion: j.motion || {} };
  if (!current || !playlists[current]) current = Object.keys(playlists)[0] || null;
  render();
}

// keepEditor: the item editor stays as it is (it's saving what you type), only the list redraws
async function savePlaylists({ keepEditor = false } = {}) {
  const j = await autosave("plSaved", () => api("/api/playlists", { playlists }));
  playlists = j.playlists; warnings = j.warnings || {};
  bus.dispatchEvent(new Event("playlists"));
  keepEditorOpen = keepEditor;
  try { render(); } finally { keepEditorOpen = false; }
}
let keepEditorOpen = false;

export async function addToPlaylist(name, item) {
  playlists[name].items.push(item);
  await savePlaylists();
}

const condLabel = id => extras.conditions.find(c => c.id === id)?.label || "";
function whenText(item) {
  const w = item.when;
  if (!w?.type) return "";
  if (w.type === "has_data" && w.minutes) return `Only when it has something within ${w.minutes} min`;
  return `Only when: ${condLabel(w.type).toLowerCase()}`;
}

function render() {
  const sel = $("plSelect");
  sel.replaceChildren(...Object.keys(playlists).map(n => el("option", { value: n, selected: n === current }, n)));
  $("plTransition").replaceChildren(...catalog.transitions.map(t =>
    el("option", { value: t, selected: playlists[current]?.transition === t }, t)));
  $("plTransDur").value = playlists[current]?.transition_duration ?? 1;
  $("plShuffle").checked = !!playlists[current]?.shuffle;
  if (!$("plAddApp").options.length) $("plAddApp").replaceChildren(...appOptions());
  const pl = playlists[current];
  const box = $("plItems");
  for (const id of ["plRename", "plDelete", "plPlay", "plAdd", "plShuffle"]) $(id).disabled = !pl;
  $("plFxDetails").hidden = !pl;
  if (!pl) {
    box.replaceChildren(el("div", { class: "empty" }, el("p", { class: "muted" }, "No playlists yet. A playlist shows apps and pictures one after another."),
      el("button", { class: "primary", onclick: () => $("plNew").click() }, "New playlist")));
    renderEditor();
    return;
  }
  if (!pl.items.length) box.replaceChildren(el("p", { class: "muted" }, "This playlist is empty. Pick an app below and press Add item, or use Add to playlist on any page that shows something."));
  else box.replaceChildren(el("div", { class: "items" }, pl.items.map((item, i) => {
    const { title, detail } = describeItem(item);
    const warn = warnings[current]?.[i];
    const cond = whenText(item);
    const dur = el("input", { type: "number", min: 1, value: item.duration, title: "Seconds on the board", "aria-label": "Seconds on the board" });
    const trans = el("select", { title: "Transition into this item", "aria-label": "Transition into this item" },
      el("option", { value: "" }, "Playlist transition"),
      catalog.transitions.map(t => el("option", { value: t, selected: item.transition === t }, t)));
    trans.onchange = safe(async () => { if (trans.value) item.transition = trans.value; else { delete item.transition; delete item.transition_duration; } await savePlaylists(); });
    dur.onchange = safe(async () => { item.duration = Math.max(1, +dur.value || 30); await savePlaylists(); });
    const move = d => safe(async () => {
      const j = i + d;
      if (j < 0 || j >= pl.items.length) return;
      [pl.items[i], pl.items[j]] = [pl.items[j], pl.items[i]];
      await savePlaylists();
    });
    return el("div", { class: "item" + (editing === i ? " editing" : "") },
      el("div", { class: "name" }, title, el("small", {}, detail),
        cond ? el("small", { class: "condText" }, cond) : null,
        warn ? el("small", { class: "warnText" }, `Needs setup: ${warn}. It's skipped until then.`) : null),
      el("label", { class: "muted" }, dur, " s"), trans,
      el("div", { class: "group" },
        iconButton("up", "Move up", move(-1)),
        iconButton("down", "Move down", move(1)),
        iconButton("edit", editing === i ? "Close editor" : "Edit settings and conditions", () => { editing = editing === i ? null : i; draft = null; render(); }),
        iconButton("play", "Show this item on the board now", safe(() => api("/api/run", { app: item.app, params: item.params }))),
        iconButton("x", "Remove from playlist", safe(async () => {
          const [gone] = pl.items.splice(i, 1);
          editing = null;
          await savePlaylists();
          const name = current;  // saving swaps in fresh objects from the server, so look the playlist up again
          undoable(`Removed ${title}`, { undo: async () => { playlists[name]?.items.splice(i, 0, gone); await savePlaylists(); } });
        }), "danger")));
  })));
  if (pl.shuffle && pl.items.length > 1) box.append(el("p", { class: "muted" }, "Shuffled: every item plays once per round, in a new order each round, never the same one twice in a row."));
  renderEditor();
  renderFx();
}

// "Only show it when…" and the transition into the item, under the item's own options
function itemExtras(item) {
  const cond = el("select", { id: "plWhen" }, extras.conditions.map(c => el("option", { value: c.id, selected: (item.when?.type || "") === c.id }, c.label)));
  const mins = el("input", { id: "plWhenMins", type: "number", min: 1, max: 1440, value: item.when?.minutes ?? "", placeholder: "any time", inputmode: "numeric", style: "width:96px" });
  const minsRow = el("div", { class: "row tight" }, mins, el("span", { class: "muted" }, "minutes ahead at most (departures, events). Empty: anything to show."));
  const trans = el("select", { id: "plItemTrans" }, el("option", { value: "" }, "The playlist's transition"),
    catalog.transitions.map(t => el("option", { value: t, selected: item.transition === t }, t)));
  const tdur = el("input", { id: "plItemTransDur", type: "number", min: 0.1, max: 10, step: 0.1, inputmode: "decimal", style: "width:72px",
    value: item.transition_duration ?? "", placeholder: "1", "aria-label": "Transition seconds" });
  const hint = el("p", { class: "help" });
  const minsLabel = el("label", { for: "plWhenMins" }, "Within");
  const sync = () => {
    minsRow.hidden = minsLabel.hidden = cond.value !== "has_data";
    tdur.disabled = !trans.value;
    hint.textContent = {
      music: "Live music from Settings → Music.", no_music: "Live music from Settings → Music.",
      dark: "From sunset to sunrise at the weather location (Settings → Weather); 19:00 to 07:00 without one.",
      daylight: "From sunrise to sunset at the weather location (Settings → Weather); 07:00 to 19:00 without one.",
      present: "Needs the mirror camera running (Mirror page).", absent: "Needs the mirror camera running (Mirror page).",
      has_data: "For apps that fetch something, such as departures or a calendar: skipped while there's nothing to show.",
    }[cond.value] || "When it can't show, the playlist goes on to the next item. If none can, what's on stays.";
  };
  cond.addEventListener("change", sync);
  trans.addEventListener("change", sync);
  sync();
  const el_ = el("div", { class: "form plItemExtras" },
    el("label", { for: "plWhen" }, "Only show it"), el("div", {}, cond, hint),
    minsLabel, minsRow,
    el("label", { for: "plItemTrans" }, "Transition into it"), el("div", { class: "row tight" }, trans, tdur, el("span", { class: "muted" }, "s")));
  const values = () => {
    const out = {};
    if (cond.value) out.when = { type: cond.value, ...(cond.value === "has_data" && +mins.value ? { minutes: Math.round(+mins.value) } : {}) };
    if (trans.value) {
      out.transition = trans.value;
      if (+tdur.value) out.transition_duration = +tdur.value;
    }
    return out;
  };
  return { el: el_, values };
}

function applyExtras(item, x) {
  for (const k of ["when", "transition", "transition_duration"]) delete item[k];
  Object.assign(item, x);
}

function renderEditor() {
  if (keepEditorOpen) return;
  const pl = playlists[current];
  const box = $("plEditor");
  const item = draft || (editing !== null && pl?.items[editing]);
  if (!item) return box.replaceChildren();
  const app = catalog.byId[item.app];
  const form = paramForm(app, item.params);
  const more = itemExtras(item);
  if (draft) {
    // a new item joins the playlist only when you add it
    box.replaceChildren(el("h3", {}, `New item: ${app.title}`), form.el, more.el,
      el("div", { class: "row" },
        el("button", { class: "primary", onclick: safe(async () => {
          draft.params = form.values();
          applyExtras(draft, more.values());
          pl.items.push(draft); draft = null;
          await savePlaylists();
          toast(`Added ${app.title}`);
        }) }, "Add to playlist"),
        el("button", { onclick: () => { draft = null; render(); } }, "Cancel")));
    return;
  }
  // an item that's already in the playlist saves itself as you change it
  let timer = null;
  const name = current, index = editing;
  const changed = () => {
    clearTimeout(timer);
    timer = setTimeout(safe(async () => {
      const live = playlists[name]?.items[index];  // saving swaps in fresh objects from the server
      if (!live || editing !== index || current !== name) return;
      live.params = form.values();
      applyExtras(live, more.values());
      await savePlaylists({ keepEditor: true });
    }), 400);
  };
  for (const f of [form.el, more.el]) { f.addEventListener("input", changed); f.addEventListener("change", changed); }
  box.replaceChildren(el("h3", {}, `Edit: ${app.title}`), form.el, el("h4", { class: "subhead" }, "When and how it comes on"), more.el,
    el("div", { class: "row" },
      el("button", { onclick: () => { editing = null; render(); } }, "Done"),
      el("span", { class: "muted" }, "Changes save as you make them.")));
}

// ---- random effects and React to music (per playlist) ----

let fx = null;  // the controls, built once; fill() shows the current playlist's settings
const pct = v => (+v ? `${v}%` : "Off");

async function saveFx(patch) {
  const pl = playlists[current];
  if (!pl) return;
  Object.assign(pl, patch);
  await savePlaylists({ keepEditor: true });
}

function buildFx() {
  const chance = el("input", { id: "plFxChance", type: "range", min: 0, max: 100, step: 5 });
  const chanceVal = el("output", { class: "rangeVal", for: "plFxChance" });
  const chips = el("div", { class: "chips", role: "group", "aria-labelledby": "plFxEffectsLabel" });
  const modes = el("div", { class: "group", role: "group", "aria-labelledby": "plFxModeLabel" },
    el("button", { "data-mode": "burst" }, "A short burst"), el("button", { "data-mode": "whole" }, "The whole item"));
  const secs = el("input", { id: "plFxSeconds", type: "number", min: 0.3, max: 30, step: 0.1, inputmode: "decimal", style: "width:80px" });
  const secsNote = el("span", { class: "muted" });
  const strength = el("input", { id: "plFxStrength", type: "range", min: 0, max: 100, step: 5 });
  const strengthVal = el("output", { class: "rangeVal", for: "plFxStrength" });
  const react = el("input", { id: "plReact", type: "checkbox" });
  const status = el("p", { class: "reactStatus", role: "status" });
  const table = reactTable({ onChange: rows => saveFx({ react_map: rows }), motion: extras.motion });
  const fallback = el("select", { id: "plReactFallback" }, el("option", { value: "none" }, "Do nothing"), el("option", { value: "beat" }, "Use a generated beat (120 BPM)"));
  const body = el("div", {}, table.el,
    el("p", { class: "help" }, "Amount is how much each effect does at full signal; Speed is how fast it moves. The same table as slideshows: see a slideshow's display options for the details."),
    el("div", { class: "form" }, el("label", { for: "plReactFallback" }, "Without live music"), fallback));

  chance.oninput = () => (chanceVal.textContent = pct(chance.value));
  chance.onchange = safe(() => saveFx({ fx_chance: +chance.value }));
  strength.oninput = () => (strengthVal.textContent = `${strength.value}%`);
  strength.onchange = safe(() => saveFx({ fx_strength: +strength.value }));
  secs.onchange = safe(() => saveFx({ fx_seconds: Math.max(0.3, Math.min(30, +secs.value || 3)) }));
  modes.addEventListener("click", e => { const b = e.target.closest("[data-mode]"); if (b) safe(() => saveFx({ fx_mode: b.dataset.mode }))(); });
  react.onchange = safe(() => saveFx({ react_enabled: react.checked }));
  fallback.onchange = safe(() => saveFx({ react_fallback: fallback.value }));

  $("plFx").replaceChildren(
    el("p", { class: "muted" }, "Now and then, give an item an effect such as a glitch or a ripple, on top of whatever it shows: a clock, a video, a layer set. Set the chance above Off to turn them on."),
    el("div", { class: "form" },
      el("label", { for: "plFxChance" }, "Chance per item"), el("div", { class: "row tight" }, chance, chanceVal),
      el("label", { id: "plFxEffectsLabel" }, "Effects"), chips,
      el("label", { id: "plFxModeLabel" }, "Apply to"), el("div", {}, modes),
      el("label", { for: "plFxSeconds" }, "Burst length"), el("div", { class: "row tight" }, secs, secsNote),
      el("label", { for: "plFxStrength" }, "Strength"), el("div", { class: "row tight" }, strength, strengthVal)),
    el("h3", {}, "React to music"),
    el("p", { class: "muted" }, "Let the music playing in the room drive effects on every item: the bass can glitch it, each beat can flash it. Games being played are left alone. Paused during quiet hours."),
    el("div", { class: "form" }, el("label", { for: "plReact" }, "React to music"), react),
    status, body);

  return {
    fill(pl) {
      const all = extras.effects.map(f => f.id);
      const chosen = new Set(pl.fx_effects ?? all);
      chance.value = pl.fx_chance ?? 0;
      chanceVal.textContent = pct(pl.fx_chance ?? 0);
      chips.replaceChildren(...extras.effects.map(({ id, label }) => el("button", {
        "aria-pressed": String(chosen.has(id)), class: chosen.has(id) ? "active" : "",
        onclick: safe(async () => {
          const next = all.filter(f => (f === id) !== chosen.has(f));
          if (!next.length) return toast("Keep at least one effect selected", true);
          await saveFx({ fx_effects: next });
        }),
      }, label)));
      const mode = pl.fx_mode || "burst";
      modes.querySelectorAll("[data-mode]").forEach(b => { b.classList.toggle("active", b.dataset.mode === mode); b.setAttribute("aria-pressed", String(b.dataset.mode === mode)); });
      secs.value = pl.fx_seconds ?? 3;
      secs.disabled = mode !== "burst";
      secsNote.textContent = mode === "burst" ? "seconds, at a random moment in the item" : "Not used: the effect lasts the whole item.";
      strength.value = pl.fx_strength ?? 50;
      strengthVal.textContent = `${pl.fx_strength ?? 50}%`;
      react.checked = !!pl.react_enabled;
      body.hidden = !pl.react_enabled;
      fallback.value = pl.react_fallback || "none";
      table.fill(pl.react_map?.length ? pl.react_map : extras.react_defaults, fx.for !== current);
      fx.for = current;
    },
    status, table, fallback, react,
  };
}

function renderFx() {
  const pl = playlists[current];
  if (!pl || !extras.effects.length) return;
  fx ||= buildFx();
  fx.fill(pl);
  pollLevels(true);
}

// the status line and meters: polled while the section is open and on screen
let levelsTimer = null, levelsBusy = false;
async function fetchLevels() {
  if (levelsBusy || !fx) return;
  levelsBusy = true;
  try {
    const j = await api(`/api/audio/levels${fx.fallback.value === "beat" ? "?fallback=beat" : ""}`);
    setStatus(fx.status, j);
    if (fx.react.checked) fx.table.setMeters(j);
  } catch { /* the next poll tries again */ } finally { levelsBusy = false; }
}
function pollLevels(now = false) {
  clearInterval(levelsTimer);
  levelsTimer = null;
  if (!fx || !$("plFxDetails").open || $("tab-playlists").hidden || document.hidden) return;
  if (now) fetchLevels();
  levelsTimer = setInterval(fetchLevels, fx.react.checked ? 250 : 3000);
}
$("plFxDetails").addEventListener("toggle", () => pollLevels(true));
document.addEventListener("visibilitychange", () => pollLevels(true));
bus.addEventListener("tabShown", () => pollLevels(true));

$("plSelect").onchange = () => { flushUndo(); current = $("plSelect").value; editing = null; draft = null; render(); };
$("plTransition").onchange = safe(async () => { playlists[current].transition = $("plTransition").value; await savePlaylists(); });
$("plTransDur").onchange = safe(async () => { playlists[current].transition_duration = Math.max(0.1, +$("plTransDur").value || 1); await savePlaylists(); });
$("plShuffle").onchange = safe(async () => { playlists[current].shuffle = $("plShuffle").checked; await savePlaylists(); });
$("plNew").onclick = safe(async () => {
  const name = await ask({ title: "New playlist", label: "Name", confirmText: "Create", validate: nameFree(Object.keys(playlists)) });
  if (!name) return;
  playlists[name] = { transition: "dissolve", items: [] };
  current = name;
  await savePlaylists();
});
$("plRename").onclick = safe(async () => {
  if (!current) return;
  const name = await ask({ title: "Rename playlist", label: "Name", value: current, confirmText: "Rename", validate: nameFree(Object.keys(playlists), current) });
  if (!name || name === current) return;
  flushUndo();
  // the server updates the schedule and automations that use the old name too
  const j = await api("/api/playlists/rename", { old: current, new: name });
  current = name;
  await loadPlaylists();
  bus.dispatchEvent(new Event("playlists"));
  bus.dispatchEvent(new Event("refs"));
  toast(j.updated ? `Renamed. Updated ${j.updated} place${j.updated === 1 ? "" : "s"} that used it.` : "Renamed");
});
$("plDelete").onclick = safe(async () => {
  if (!current || !await confirmDelete("playlist", current, `the playlist "${current}"`)) return;
  flushUndo();
  await api("/api/playlists/delete", { name: current });
  current = null;
  await loadPlaylists();
  bus.dispatchEvent(new Event("playlists"));
  bus.dispatchEvent(new Event("refs"));
});
$("plPlay").onclick = safe(async () => { if (current) { await api("/api/playlist/play", { name: current }); toast(`Playing "${current}"`); } });
$("plAdd").onclick = safe(async () => {
  if (!current) return toast("Create a playlist first", true);
  const app = catalog.byId[$("plAddApp").value];
  draft = { app: app.id, params: paramForm(app).values(), duration: 30 };
  editing = null;
  render();
  $("plEditor").scrollIntoView({ block: "nearest" });
});
// Add to playlist from another page (addto.js): reload, and open the playlist when asked
bus.addEventListener("playlistAdded", () => loadPlaylists().catch(e => toast(e.message, true)));
bus.addEventListener("openPlaylist", safe(async e => {
  current = e.detail.name;
  editing = null; draft = null;
  await loadPlaylists();
  bus.dispatchEvent(new CustomEvent("tab", { detail: "playlists" }));
  const rows = $("plItems").querySelectorAll(".item");
  rows[e.detail.index ?? rows.length - 1]?.scrollIntoView({ block: "center" });
}));
bus.addEventListener("scenes", () => render());
bus.addEventListener("refs", () => loadPlaylists().catch(e => toast(e.message, true)));
