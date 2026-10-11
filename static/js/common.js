// Shared helpers: API calls, board state polling, dot rendering, toasts.
import { GAME_GROUPS } from "./games.js";

export const $ = id => document.getElementById(id);
export const bus = new EventTarget();
export const board = { W: 84, H: 42, grid: [], version: -1, runner: null };

export async function api(path, body, opts = {}) {
  const init = body === undefined ? {} : {
    method: "POST",
    headers: opts.raw ? {} : { "Content-Type": "application/json" },
    body: opts.raw ? body : JSON.stringify(body),
  };
  const r = await fetch(path, init);
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  if (j.rows && j.runner) applyState(j);
  return j;
}

// Errors stay until dismissed; ones that need a setting carry a button that opens it.
export function toast(msg, isError = false, action = null, ms = 2500) {
  const t = $("toast");
  action ||= isError ? fixFor(msg) : null;
  t.replaceChildren(el("span", {}, msg));
  if (action) t.append(el("button", { type: "button", onclick: () => { t.hidden = true; action.onclick(); } }, action.label));
  if (isError) t.append(el("button", { type: "button", class: "close", "aria-label": "Dismiss", onclick: () => { t.hidden = true; } }, "×"));
  t.classList.toggle("error", isError);
  t.setAttribute("role", isError ? "alert" : "status");
  t.hidden = false;
  clearTimeout(toast.timer);
  if (!isError) toast.timer = setTimeout(() => (t.hidden = true), ms);
}

// Messages that name a setting get a button that goes there: [pattern, button label, settings group, field to focus]
const FIXES = [
  [/AI drawing|Anthropic/i, "Open Settings → AI drawing", "ai", "aiKey"],
  [/weather location|Settings, Location/i, "Open Settings → Location", "location", "wxSearch"],
  [/Music Assistant URL|Settings, Music/i, "Open Settings → Music", "music", "maUrl"],
  [/MQTT/, "Open Settings → Smart home", "home", "mqHost"],
];
export function fixFor(msg) {
  const f = FIXES.find(([re]) => re.test(msg || ""));
  return f ? { label: f[1], onclick: () => openSettings(f[2], f[3]) } : null;
}
export function openSettings(group, field) {
  bus.dispatchEvent(new CustomEvent("tab", { detail: `settings/${group}` }));
  const f = field && $(field);
  if (f) { f.scrollIntoView({ block: "center" }); f.focus({ preventScroll: true }); }
}

// One-click removals: the item goes now, and the toast offers Undo for a few seconds.
// `commit` (optional) runs once the window closes without Undo, e.g. to delete a file for real.
let pendingUndo = null;
export function undoable(msg, { undo, commit = null, ms = 7000 }) {
  flushUndo();
  const p = pendingUndo = { commit, timer: setTimeout(() => { if (pendingUndo === p) flushUndo(); }, ms) };
  toast(msg, false, { label: "Undo", onclick: safe(async () => {
    if (pendingUndo !== p) return;
    clearTimeout(p.timer);
    pendingUndo = null;
    await undo();
  }) }, ms);
}
// Finish a pending removal now (call before anything that would save over it).
export function flushUndo() {
  const p = pendingUndo;
  if (!p) return;
  pendingUndo = null;
  clearTimeout(p.timer);
  if (p.commit) safe(p.commit)();
}

// Before deleting a playlist, layer set, scene, slideshow or drawing: list what uses it and ask.
// The server removes those uses (or turns off automations) when the thing is deleted.
export async function confirmDelete(kind, key, label) {
  let used = [];
  try { used = (await api(`/api/refs?kind=${kind}&key=${encodeURIComponent(key)}`)).used_by; } catch {}
  const more = used.length > 8 ? [el("li", {}, `…and ${used.length - 8} more`)] : [];
  return confirmDialog({
    title: `Delete ${label}?`, confirmText: "Delete", danger: true,
    message: used.length ? [
      el("p", {}, "It's used by:"), el("ul", {}, used.slice(0, 8).map(u => el("li", {}, u)), more),
      el("p", {}, "Those playlist items and layers are removed, schedule rules switch to Nothing and automations are turned off."),
    ] : "This can't be undone.",
  });
}

// ---- one dialog for naming things and confirming (instead of the browser's prompt() and confirm()) ----
let dlg = null;
// ask({ title, label, value, confirmText, validate(v) -> error text or null }) resolves to the text, or null on Cancel.
// With input: false it's a yes/no question and resolves to true/false.
export function ask({ title, label = "Name", value = "", message = null, confirmText = "OK", danger = false, validate = null, input = true }) {
  dlg?.close();
  const field = input ? el("input", { type: "text", value, maxlength: 120, "aria-describedby": "dlgErr", autocomplete: "off" }) : null;
  const err = el("p", { class: "dlgErr", id: "dlgErr", role: "alert" });
  const ok = el("button", { type: "submit", value: "ok", class: danger ? "dangerFill" : "primary" }, confirmText);
  const form = el("form", { method: "dialog" },
    el("h2", { id: "dlgTitle" }, title),
    message ? el("div", { class: "muted dlgMsg" }, message) : null,
    field ? el("label", { class: "dlgField" }, el("span", {}, label), field) : null,
    err,
    el("div", { class: "row dlgButtons" }, el("button", { type: "button", value: "cancel", onclick: () => d.close("cancel") }, "Cancel"), ok));
  const d = dlg = el("dialog", { class: "dlg", "aria-labelledby": "dlgTitle" }, form);
  document.body.append(d);
  return new Promise(resolve => {
    form.addEventListener("submit", e => {
      if (!field) return;
      const v = field.value.trim();
      const problem = !v ? `Enter a ${label.toLowerCase()}.` : validate?.(v);
      if (problem) { e.preventDefault(); err.textContent = problem; field.focus(); }
    });
    field?.addEventListener("input", () => { err.textContent = ""; });
    d.addEventListener("close", () => {
      const yes = d.returnValue === "ok";
      d.remove();
      if (dlg === d) dlg = null;
      resolve(field ? (yes ? field.value.trim() : null) : yes);
    });
    d.showModal();
    if (field) { field.focus(); field.select(); } else ok.focus();
  });
}
export const confirmDialog = opts => ask({ ...opts, input: false });
// choose({ title, message, choices: [[value, label, primary?]] }) resolves to the chosen value, or null on Cancel.
export function choose({ title, message = null, choices }) {
  dlg?.close();
  const form = el("form", { method: "dialog" },
    el("h2", { id: "dlgTitle" }, title),
    message ? el("div", { class: "muted dlgMsg" }, message) : null,
    el("div", { class: "row dlgButtons" },
      el("button", { type: "button", onclick: () => d.close("") }, "Cancel"),
      ...choices.map(([value, label, primary]) => el("button", { type: "submit", value, class: primary ? "primary" : "" }, label))));
  const d = dlg = el("dialog", { class: "dlg", "aria-labelledby": "dlgTitle" }, form);
  document.body.append(d);
  return new Promise(resolve => {
    d.addEventListener("close", () => {
      d.remove();
      if (dlg === d) dlg = null;
      resolve(d.returnValue || null);
    });
    d.showModal();
    form.querySelector("button.primary")?.focus();
  });
}

// POST a file's bytes with upload progress: onprogress(loaded, total). Resolves to the JSON reply, throws its error.
export function uploadFile(path, file, onprogress = null) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", path);
    if (onprogress) x.upload.onprogress = e => onprogress(e.loaded, e.total || file.size);
    x.onload = () => {
      let j = {};
      try { j = JSON.parse(x.responseText); } catch {}
      if (x.status >= 400) reject(new Error(j.error || `upload failed (HTTP ${x.status})`));
      else resolve(j);
    };
    x.onerror = () => reject(new Error("the upload was cut off. Check the connection and try again."));
    x.send(file);
  });
}
// Name validation shared by New / Rename / Save as: taken names are refused in the dialog itself.
export const nameFree = (names, except = null) => v => (v !== except && names.includes(v) ? `"${v}" is already taken.` : null);

// ---- autosave feedback: "Saving…" then "Saved" next to whatever was edited ----
export async function autosave(target, fn, done = "Saved") {
  const note = typeof target === "string" ? $(target) : target;
  if (note) { note.textContent = "Saving…"; note.classList.remove("bad"); }
  try {
    const r = await fn();
    if (note) note.replaceChildren(icon("check"), done);
    return r;
  } catch (e) {
    if (note) { note.textContent = "Not saved"; note.classList.add("bad"); }
    throw e;
  }
}

// Settings fields: fill from the server, but never over something the user typed and hasn't saved.
const shown = new Map();  // id -> [prop, value we last put there]
export function fillField(id, value, prop = "value") {
  const e = $(id);
  const last = shown.get(id);
  if (last && e[prop] !== last[1]) return;  // edited since: keep the draft
  e[prop] = value;
  shown.set(id, [prop, e[prop]]);
}
export const forgetFields = ids => ids.forEach(id => shown.delete(id));
export const unsavedFields = () => [...shown].filter(([id, [prop, v]]) => $(id) && $(id)[prop] !== v).map(([id]) => id);

// Wrap an async handler so errors show up (next to the button that failed, or as a toast), and a clicked
// button stays busy until the work is done, so a double click doesn't do it twice.
export const safe = fn => async (...a) => {
  const btn = a[0]?.currentTarget instanceof HTMLButtonElement ? a[0].currentTarget : null;
  if (btn) {
    if (btn.getAttribute("aria-busy") === "true") return;
    btn.setAttribute("aria-busy", "true");
    btn.disabled = true;
    clearInlineError(btn);
  }
  try { await fn(...a); } catch (e) { if (!(btn && inlineError(btn, e.message))) toast(e.message, true); }
  finally {
    if (btn) { btn.removeAttribute("aria-busy"); btn.disabled = false; }
  }
};

// An error shown right after the control that failed; it stays until dismissed or the control is used again.
function inlineError(btn, msg) {
  const anchor = btn.closest(".group") || btn;
  if (!btn.isConnected || !anchor.parentElement?.matches(".row, .bar")) return false;
  clearInlineError(btn);
  const fix = fixFor(msg);
  const box = el("span", { class: "inlineErr", role: "alert" }, el("span", {}, msg),
    fix ? el("button", { type: "button", onclick: () => { box.remove(); fix.onclick(); } }, fix.label) : null,
    el("button", { type: "button", class: "close", "aria-label": "Dismiss", onclick: () => box.remove() }, "×"));
  anchor.after(box);
  return true;
}
function clearInlineError(btn) {
  const anchor = btn.closest(".group") || btn;
  if (anchor.nextElementSibling?.classList.contains("inlineErr")) anchor.nextElementSibling.remove();
}

export const rowsToGrid = rows => rows.map(r => [...r].map(c => c === "1"));
export const gridToRows = grid => grid.map(r => r.map(v => (v ? "1" : "0")).join(""));
export const cloneGrid = g => g.map(r => r.slice());
export const blankGrid = (w = board.W, h = board.H, v = false) => Array.from({ length: h }, () => Array(w).fill(v));

export function applyState(s) {
  const changed = s.version !== board.version || JSON.stringify(s.runner) !== JSON.stringify(board.runner);
  board.W = s.width; board.H = s.height; board.version = s.version; board.runner = s.runner;
  board.grid = rowsToGrid(s.rows);
  if (changed) bus.dispatchEvent(new Event("state"));
}

// Discs are pre-rendered once per size (shaded like real flipdots, with the axle line),
// then stamped — nicer and much faster than drawing 3,528 arcs every frame.
const discCache = new Map();
function discSprites(cell) {
  if (discCache.has(cell)) return discCache.get(cell);
  const make = (lit) => {
    const c = document.createElement("canvas");
    c.width = c.height = cell;
    const g = c.getContext("2d"), r = cell * 0.43, m = cell / 2;
    const grad = g.createRadialGradient(m - r * 0.35, m - r * 0.35, r * 0.1, m, m, r);
    if (lit) { grad.addColorStop(0, "#fffbf0"); grad.addColorStop(1, "#d9d1bd"); }
    else { grad.addColorStop(0, "#232323"); grad.addColorStop(1, "#121212"); }
    g.fillStyle = grad;
    g.beginPath(); g.arc(m, m, r, 0, Math.PI * 2); g.fill();
    if (cell >= 10) {  // the pivot axle across the disc
      g.strokeStyle = lit ? "rgba(0,0,0,0.12)" : "rgba(255,255,255,0.05)";
      g.lineWidth = Math.max(1, cell / 18);
      g.beginPath(); g.moveTo(m - r, m); g.lineTo(m + r, m); g.stroke();
    }
    return c;
  };
  const pair = { on: make(true), off: make(false) };
  discCache.set(cell, pair);
  return pair;
}

export function renderDots(ctx, grid, cell, { seams = true, overlay = null } = {}) {
  const h = grid.length, w = grid[0]?.length || 0;
  const { on, off } = discSprites(cell);
  ctx.fillStyle = "#050505";
  ctx.fillRect(0, 0, w * cell, h * cell);
  for (let y = 0; y < h; y++) {
    const row = grid[y], ov = overlay && overlay[y];
    for (let x = 0; x < w; x++) {
      const v = ov && ov[x] !== undefined && ov[x] !== null ? ov[x] : row[x];
      ctx.drawImage(v ? on : off, x * cell, y * cell);
    }
  }
  if (seams && cell >= 4) {  // panel joins, barely there
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    for (let x = 28; x < w; x += 28) { ctx.beginPath(); ctx.moveTo(x * cell + 0.5, 0); ctx.lineTo(x * cell + 0.5, h * cell); ctx.stroke(); }
    for (let y = 7; y < h; y += 7) { ctx.beginPath(); ctx.moveTo(0, y * cell + 0.5); ctx.lineTo(w * cell, y * cell + 0.5); ctx.stroke(); }
  }
}

// Inline icon from the sprite in index.html
export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "i");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

// Compact icon-only button with an accessible name
export function iconButton(name, label, onclick, extra = "") {
  const b = el("button", { class: `icon ghost ${extra}`.trim(), title: label, "aria-label": label, onclick });
  b.append(icon(name));
  return b;
}

// Small static thumbnail canvas for a frame
export function thumbCanvas(rows, cell = 3) {
  const c = document.createElement("canvas");
  const grid = rowsToGrid(rows);
  c.width = grid[0].length * cell; c.height = grid.length * cell;
  renderDots(c.getContext("2d"), grid, cell, { seams: false });
  return c;
}

// ---- live updates: WebSocket push, with HTTP polling as a fallback ----

let pollPaused = 0;
export const pausePolling = on => (pollPaused += on ? 1 : -1);

let ws = null, wsOpen = false, retry = 500, failures = 0;
export const live = () => wsOpen;

// Send over the WebSocket if it's up; returns false so callers can fall back to HTTP.
export function wsSend(msg) {
  if (!wsOpen) return false;
  ws.send(JSON.stringify(msg));
  return true;
}

// Bytes queued on the socket but not sent yet: streams skip a frame rather than pile up lag.
export const wsBacklog = () => (wsOpen ? ws.bufferedAmount : 0);

function applyPacked(buf) {
  const dv = new DataView(buf);
  const version = dv.getUint32(0), W = dv.getUint16(4), H = dv.getUint16(6);
  const bits = new Uint8Array(buf, 8);
  if (pollPaused > 0 || version === board.version) return;  // don't clobber a stroke in progress
  const grid = [];
  for (let y = 0, i = 0; y < H; y++) {
    const row = new Array(W);
    for (let x = 0; x < W; x++, i++) row[x] = (bits[i >> 3] & (0x80 >> (i & 7))) !== 0;
    grid.push(row);
  }
  board.W = W; board.H = H; board.version = version; board.grid = grid;
  bus.dispatchEvent(new Event("state"));
}

function connect() {
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws" + location.search);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { wsOpen = true; retry = 500; failures = 0; bus.dispatchEvent(new Event("live")); };
  ws.onmessage = e => {
    if (typeof e.data !== "string") return applyPacked(e.data);
    const m = JSON.parse(e.data);
    if (m.type === "status") {
      board.runner = m.runner;
      bus.dispatchEvent(new Event("state"));
    } else if (m.type === "error") {
      toast(m.error, true);
    }
  };
  ws.onclose = () => {
    if (wsOpen) bus.dispatchEvent(new Event("reconnecting"));
    wsOpen = false;
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 5000);
  };
}

async function poll() {
  // Only used while the WebSocket is down (or not yet connected).
  if (!wsOpen && pollPaused <= 0) {
    try {
      const r = await fetch("/api/board", { cache: "no-store" });
      applyState(await r.json());
      failures = 0;
    } catch {
      // one dropped request isn't worth alarming anyone about
      if (++failures >= 3) bus.dispatchEvent(new CustomEvent("offline"));
    }
  }
  setTimeout(poll, wsOpen ? 1000 : 500);
}

export function startPolling() {
  if ("WebSocket" in window) connect();
  poll();
}

export function download(name, blob) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

export function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) e.append(c);
  return e;
}

// ---- app catalog + parameter forms (used by Apps and Playlists tabs) ----

export const catalog = { apps: [], byId: {}, transitions: [] };
export const scenesCache = { list: [] };
export const compCache = { names: [] };
export const fontCache = { list: [] };
export const drawingsCache = { list: [] };
export const galleriesCache = { list: [] };
export const videosCache = { list: [] };  // the Videos library (videos.js keeps it current)
// uploaded NES ROMs (GET /api/nes/roms); "nesroms" fires on the bus when the list changes
export const nesCache = { list: [], built: true, loaded: false };
export async function loadNesRoms() {
  const j = await api("/api/nes/roms");
  Object.assign(nesCache, { list: j.roms, built: j.built, max_bytes: j.max_bytes, loaded: true });
  bus.dispatchEvent(new Event("nesroms"));
  return nesCache;
}

export async function loadDrawings() {
  drawingsCache.list = (await api("/api/drawings")).drawings;
  bus.dispatchEvent(new Event("drawings"));
}

export async function loadFonts() {
  fontCache.list = (await api("/api/fonts")).fonts;
  bus.dispatchEvent(new Event("fonts"));
}

// <select> of fonts grouped into pixel / uploaded / system
export function fontSelect(value, attrs = {}) {
  const groups = { pixel: "Pixel fonts", uploaded: "Your fonts", system: "Installed fonts" };
  return el("select", attrs, Object.entries(groups).map(([kind, label]) => {
    const items = fontCache.list.filter(f => f.kind === kind);
    return items.length ? el("optgroup", { label }, items.map(f => el("option", { value: f.id, selected: f.id === value }, f.label))) : null;
  }));
}

// The order apps are offered in, everywhere: everyday things first, games last.
// The "Used in playlists" apps only show something you made elsewhere (a picture, drawing, layer set or slideshow).
const EVERYDAY = ["clock", "text", "weather", "calendar", "music", "analog_clock", "countdown", "guest_wall"];
export const APP_SECTIONS = [
  ["everyday", "Everyday", a => EVERYDAY.includes(a.id)],
  ["widgets", "Widgets", a => a.category === "Widgets"],
  ["generative", "Generative", a => a.category === "Generative"],
  ["utilities", "Utilities", a => ["Utilities", "Clocks", "Text"].includes(a.category)],
  ["plugins", "Plugins", a => a.category === "Plugins"],
  ["other", "More", a => !["Games", "Layers", "Media"].includes(a.category)],
  ["playlists", "Used in playlists", a => ["Layers", "Media"].includes(a.category)],
  ["games", "Games", () => true],
];
const GAME_ORDER = Object.values(GAME_GROUPS).flat().map(([id]) => id);
const gameGroupOf = id => Object.entries(GAME_GROUPS).find(([, g]) => g.some(([gid]) => gid === id))?.[0] || "More games";
export function appSections(apps = catalog.apps) {
  const out = APP_SECTIONS.map(([key, label]) => ({ key, label, apps: [] }));
  for (const a of apps) out[APP_SECTIONS.findIndex(([, , test]) => test(a))].apps.push(a);
  out[0].apps.sort((a, b) => EVERYDAY.indexOf(a.id) - EVERYDAY.indexOf(b.id));
  const games = out.find(s => s.key === "games");
  games.apps.sort((a, b) => (GAME_ORDER.indexOf(a.id) + 1 || 999) - (GAME_ORDER.indexOf(b.id) + 1 || 999));
  games.groups = {};
  for (const a of games.apps) (games.groups[gameGroupOf(a.id)] ||= []).push(a);
  return out.filter(s => s.apps.length);
}
// <option>s for an app picker, grouped in the same order (skip: app ids to leave out)
export function appOptions(selected = null, skip = []) {
  return appSections(catalog.apps.filter(a => !skip.includes(a.id))).map(s =>
    el("optgroup", { label: s.label }, s.apps.map(a => el("option", { value: a.id, selected: a.id === selected }, a.title))));
}

export async function loadCatalog() {
  const j = await api("/api/apps");
  catalog.apps = j.apps; catalog.transitions = j.transitions;
  catalog.byId = Object.fromEntries(j.apps.map(a => [a.id, a]));
}

export async function refreshScenes() {
  scenesCache.list = (await api("/api/scenes")).scenes;
  bus.dispatchEvent(new Event("scenes"));
  return scenesCache.list;
}

// Param types other modules build (musicreact.js): type -> (param, value, id) => { input, get }
export const paramTypes = {};

// Build a form for an app's params. Returns { el, values() }.
export function paramForm(app, values = {}) {
  const form = el("div", { class: "form" });
  const getters = {}, shown = [];
  for (const p of app.params) {
    if (p.type === "hidden") continue;
    const id = `p_${app.id}_${p.name}_${Math.random().toString(36).slice(2, 7)}`;
    const v = values[p.name] ?? p.default;
    let input;
    if (paramTypes[p.type]) {
      ({ input, get: getters[p.name] } = paramTypes[p.type](p, v, id));
    } else if (p.type === "select") {
      input = el("select", { id }, p.options.map(([val, label]) => el("option", { value: val, selected: String(val) === String(v) }, label)));
      getters[p.name] = () => input.value;
    } else if (p.type === "scene") {
      input = el("select", { id }, scenesCache.list.map(s => el("option", { value: s.id, selected: s.id === v }, `${s.name}${s.frames > 1 ? ` (${s.frames} frames)` : ""}`)));
      if (!scenesCache.list.length) input.append(el("option", { value: "" }, "No scenes saved yet"));
      getters[p.name] = () => input.value;
    } else if (p.type === "video") {
      const ready = videosCache.list.filter(x => x.status === "ready");
      input = el("select", { id }, ready.map(x => el("option", { value: x.id, selected: x.id === v }, x.name)));
      if (!ready.length) input.append(el("option", { value: "" }, "No videos yet (add them on the Videos page)"));
      getters[p.name] = () => input.value;
    } else if (p.type === "gallery") {
      input = el("select", { id }, galleriesCache.list.map(g => el("option", { value: g.id, selected: g.id === v }, `${g.name} (${g.count})`)));
      if (!galleriesCache.list.length) input.append(el("option", { value: "" }, "No slideshows yet"));
      getters[p.name] = () => input.value;
    } else if (p.type === "drawing") {
      input = el("select", { id }, drawingsCache.list.map(d => el("option", { value: d.id, selected: d.id === v }, d.name)));
      if (!drawingsCache.list.length) input.append(el("option", { value: "" }, "No drawings yet"));
      getters[p.name] = () => input.value;
    } else if (p.type === "nesrom") {
      // fills itself from the uploaded ROMs, and again whenever that list changes
      const sel = input = el("select", { id });
      const fill = () => {
        const keep = sel.value || v;
        const roms = nesCache.list.filter(r => !r.problem);
        sel.replaceChildren(...roms.map(r => el("option", { value: r.name, selected: r.name === keep }, r.title)));
        if (!roms.length) sel.append(el("option", { value: "" }, nesCache.loaded ? "No games uploaded yet" : "Loading…"));
      };
      fill();
      let seen = false;  // once the form has been on the page and is gone, stop listening
      const onRoms = () => {
        if (sel.isConnected) seen = true;
        else if (seen) return bus.removeEventListener("nesroms", onRoms);
        fill();
      };
      bus.addEventListener("nesroms", onRoms);
      if (!nesCache.loaded) loadNesRoms().catch(() => {});
      getters[p.name] = () => sel.value;
    } else if (p.type === "font") {
      input = fontSelect(v, { id });
      getters[p.name] = () => input.value;
    } else if (p.type === "composition") {
      input = el("select", { id }, compCache.names.map(n => el("option", { value: n, selected: n === v }, n)));
      if (!compCache.names.length) input.append(el("option", { value: "" }, "No saved layer sets yet"));
      getters[p.name] = () => input.value;
    } else if (p.type === "checkbox") {
      input = el("input", { id, type: "checkbox", checked: !!v });
      getters[p.name] = () => input.checked;
    } else if (p.type === "checklist") {
      // a list of values: one switch per option
      const on = new Set((Array.isArray(v) ? v : String(v ?? "").split(",")).map(String));
      const boxes = p.options.map(([val, label]) => [val, el("input", { type: "checkbox", checked: on.has(String(val)) }), label]);
      input = el("div", { id, class: "checklist" }, boxes.map(([, box, label]) => el("label", {}, box, label)));
      getters[p.name] = () => boxes.filter(([, box]) => box.checked).map(([val]) => val);
    } else if (p.type === "number") {
      input = el("input", { id, type: "number", value: v, min: p.min, max: p.max, step: p.step ?? "any" });
      getters[p.name] = () => (input.value === "" ? p.default : Number(input.value));
    } else if (p.type === "textarea") {
      input = el("textarea", { id });
      input.value = v ?? "";
      getters[p.name] = () => input.value;
    } else if (p.type === "datetime") {
      input = el("input", { id, type: "datetime-local", value: v });
      getters[p.name] = () => input.value;
    } else {
      input = el("input", { id, type: "text", value: v ?? "" });
      getters[p.name] = () => input.value;
    }
    const label = el("label", { for: id }, p.label);
    form.append(label, input);
    if (p.show_if) shown.push([p.show_if, label, input]);
  }
  // show_if: {param: value | [values] | {not: value | [values]}}, all must hold. A font param compares its
  // kind ("pixel", "uploaded" or "system"), so fields for pixel fonts and other fonts can swap.
  const kinds = Object.fromEntries(app.params.map(p => [p.name, p.type]));
  const current = name => {
    const v = getters[name]?.();
    return kinds[name] === "font" ? (fontCache.list.find(f => f.id === v)?.kind || "pixel") : v;
  };
  const holds = (want, v) => {
    if (want && typeof want === "object" && !Array.isArray(want)) return !holds(want.not, v);
    return (Array.isArray(want) ? want : [want]).some(w => String(w) === String(v));
  };
  const update = () => shown.forEach(([cond, label, input]) => {
    const on = Object.entries(cond).every(([name, want]) => holds(want, current(name)));
    label.hidden = input.hidden = !on;
  });
  if (shown.length) { update(); form.addEventListener("input", update); form.addEventListener("change", update); }
  return { el: form, values: () => Object.fromEntries(Object.entries(getters).map(([k, g]) => [k, g()])) };
}

export function describeItem(item) {
  const app = catalog.byId[item.app];
  const title = app ? app.title : item.app;
  const p = item.params || {};
  let detail = "";
  if (item.app === "text") detail = p.text;
  else if (item.app === "scene") detail = scenesCache.list.find(s => s.id === p.scene)?.name || p.scene;
  else if (item.app === "weather") detail = p.location;
  else if (item.app === "crypto") detail = p.coins;
  else if (item.app === "stocks") detail = p.symbols;
  else if (item.app === "sports") detail = (p.league || "").toUpperCase() + (p.teams ? ` · ${p.teams}` : "");
  else if (item.app === "countdown") detail = p.label;
  else if (item.app === "blank") detail = p.color;
  else if (item.app === "layers") detail = p.composition;
  else if (item.app === "slideshow") detail = galleriesCache.list.find(g => g.id === p.gallery)?.name;
  else if (item.app === "drawing") detail = drawingsCache.list.find(d => d.id === p.drawing)?.name;
  else if (item.app === "video") detail = videosCache.list.find(x => x.id === p.video)?.name;
  return { title, detail: detail ? String(detail).slice(0, 60) : "" };
}
