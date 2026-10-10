// Shared helpers: API calls, board state polling, dot rendering, toasts.

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

export function toast(msg, isError = false) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), isError ? 5000 : 2500);
}

// Wrap an async click handler so errors show up as toasts.
export const safe = fn => async (...a) => {
  try { await fn(...a); } catch (e) { toast(e.message, true); }
};

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

// Build a form for an app's params. Returns { el, values() }.
export function paramForm(app, values = {}) {
  const form = el("div", { class: "form" });
  const getters = {};
  for (const p of app.params) {
    if (p.type === "hidden") continue;
    const id = `p_${app.id}_${p.name}_${Math.random().toString(36).slice(2, 7)}`;
    const v = values[p.name] ?? p.default;
    let input;
    if (p.type === "select") {
      input = el("select", { id }, p.options.map(([val, label]) => el("option", { value: val, selected: String(val) === String(v) }, label)));
      getters[p.name] = () => input.value;
    } else if (p.type === "scene") {
      input = el("select", { id }, scenesCache.list.map(s => el("option", { value: s.id, selected: s.id === v }, `${s.name}${s.frames > 1 ? ` (${s.frames} frames)` : ""}`)));
      if (!scenesCache.list.length) input.append(el("option", { value: "" }, "No scenes saved yet"));
      getters[p.name] = () => input.value;
    } else if (p.type === "gallery") {
      input = el("select", { id }, galleriesCache.list.map(g => el("option", { value: g.id, selected: g.id === v }, `${g.name} (${g.count})`)));
      if (!galleriesCache.list.length) input.append(el("option", { value: "" }, "No slideshows yet"));
      getters[p.name] = () => input.value;
    } else if (p.type === "drawing") {
      input = el("select", { id }, drawingsCache.list.map(d => el("option", { value: d.id, selected: d.id === v }, d.name)));
      if (!drawingsCache.list.length) input.append(el("option", { value: "" }, "No drawings yet"));
      getters[p.name] = () => input.value;
    } else if (p.type === "font") {
      input = fontSelect(v, { id });
      getters[p.name] = () => input.value;
    } else if (p.type === "composition") {
      input = el("select", { id }, compCache.names.map(n => el("option", { value: n, selected: n === v }, n)));
      if (!compCache.names.length) input.append(el("option", { value: "" }, "No compositions saved yet"));
      getters[p.name] = () => input.value;
    } else if (p.type === "checkbox") {
      input = el("input", { id, type: "checkbox", checked: !!v });
      getters[p.name] = () => input.checked;
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
    form.append(el("label", { for: id }, p.label), input);
  }
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
  return { title, detail: detail ? String(detail).slice(0, 60) : "" };
}
