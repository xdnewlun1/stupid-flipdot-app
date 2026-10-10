// Board view + Draw tab: drawing tools, undo/redo, selection, text stamp, export.
// You can draw on the board itself (which stops whatever is running) or on a drawing layer
// (which keeps the running composition going).
import {
  $, api, board, bus, renderDots, cloneGrid, gridToRows, blankGrid, pausePolling, rowsToGrid,
  safe, toast, download, refreshScenes, wsSend, fontSelect, fontCache, drawingsCache, loadDrawings, el,
} from "./common.js";

const CELL = 16;
const canvas = $("board"), ctx = canvas.getContext("2d");
let tool = "pencil", color = "white";
let active = true;               // is the Draw tab open?
let drawing = false, start = null, hover = null;
let overlay = null;              // preview grid (null = no change at that dot)
let selection = null;            // {x0,y0,x1,y1}
let clip = null, pasteMode = false;
let stampRows = null;
let pending = [], touched = new Set();
const undoStack = [], redoStack = [];
let target = null;               // null = the board, else { id, name, grid } of a drawing layer
let paused = false;

// the grid the tools work on
const G = () => (target ? target.grid : board.grid);
const onBoard = () => !target || (board.runner?.drawings || []).includes(target.id);
const showLayerOnly = () => target && ($("layerOnly").checked || !onBoard());

export function setActive(on) { active = on; canvas.style.cursor = on ? "crosshair" : "default"; redraw(); }

function redraw() {
  if (!board.grid.length) return;
  if (canvas.width !== board.W * CELL) { canvas.width = board.W * CELL; canvas.height = board.H * CELL; }
  renderDots(ctx, showLayerOnly() ? target.grid : board.grid, CELL, { overlay });
  if (!active) return;
  if (selection) {
    const { x0, y0, x1, y1 } = normSel(selection);
    ctx.setLineDash([6, 4]); ctx.strokeStyle = "#f2c94c"; ctx.lineWidth = 2;
    ctx.strokeRect(x0 * CELL + 1, y0 * CELL + 1, (x1 - x0 + 1) * CELL - 2, (y1 - y0 + 1) * CELL - 2);
    ctx.setLineDash([]);
  }
  if (hover && !overlay) {
    ctx.strokeStyle = "#f2c94c"; ctx.lineWidth = 1;
    ctx.strokeRect(hover.x * CELL + 0.5, hover.y * CELL + 0.5, CELL - 1, CELL - 1);
  }
}
bus.addEventListener("state", () => { if (!drawing) redraw(); });

// ---- helpers ----

const valueFor = old => (color === "toggle" ? !old : color === "white");
const normSel = s => ({ x0: Math.min(s.x0, s.x1), y0: Math.min(s.y0, s.y1), x1: Math.max(s.x0, s.x1), y1: Math.max(s.y0, s.y1) });

function cellAt(e) {
  const r = canvas.getBoundingClientRect();
  const x = Math.floor((e.clientX - r.left) / r.width * board.W);
  const y = Math.floor((e.clientY - r.top) / r.height * board.H);
  return { x: Math.max(0, Math.min(board.W - 1, x)), y: Math.max(0, Math.min(board.H - 1, y)), inside: x >= 0 && y >= 0 && x < board.W && y < board.H };
}

function linePts(x0, y0, x1, y1) {
  const pts = [];
  const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  for (;;) {
    pts.push([x0, y0]);
    if (x0 === x1 && y0 === y1) return pts;
    const e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
}

function shapePts(kind, a, b) {
  const pts = [];
  if (kind === "line") return linePts(a.x, a.y, b.x, b.y);
  if (kind === "rect" || kind === "rectfill") {
    const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x), y0 = Math.min(a.y, b.y), y1 = Math.max(a.y, b.y);
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++)
      if (kind === "rectfill" || x === x0 || x === x1 || y === y0 || y === y1) pts.push([x, y]);
    return pts;
  }
  const r = Math.hypot(b.x - a.x, b.y - a.y);
  for (let y = Math.floor(a.y - r - 1); y <= a.y + r + 1; y++)
    for (let x = Math.floor(a.x - r - 1); x <= a.x + r + 1; x++) {
      const d = Math.hypot(x - a.x, y - a.y);
      if (kind === "circlefill" ? d <= r + 0.5 : Math.abs(d - r) <= 0.5) pts.push([x, y]);
    }
  return pts;
}

function overlayFrom(pts, fn) {
  const o = blankGrid(board.W, board.H, null);
  for (const [x, y, v] of pts)
    if (x >= 0 && y >= 0 && x < board.W && y < board.H) o[y][x] = v !== undefined ? v : fn(G()[y][x]);
  return o;
}

// Text stamps draw their lit dots in the current color; pastes copy dots exactly.
function stampOverlay(rows, at, exact = false) {
  const pts = [];
  rows.forEach((row, dy) => [...row].forEach((ch, dx) => {
    const on = ch === "1" || ch === true;
    if (exact) pts.push([at.x + dx, at.y + dy, on]);
    else if (on) pts.push([at.x + dx, at.y + dy, valueFor(G()[at.y + dy]?.[at.x + dx])]);
  }));
  return overlayFrom(pts);
}

function applyOverlay() {
  const g = cloneGrid(G());
  overlay.forEach((row, y) => row.forEach((v, x) => { if (v !== null) g[y][x] = v; }));
  overlay = null;
  return g;
}

function pushHistory() {
  undoStack.push(cloneGrid(G()));
  if (undoStack.length > 100) undoStack.shift();
  redoStack.length = 0;
}

async function commit(grid) {
  if (target) {
    target.grid = grid;
    redraw();
    await api(`/api/drawings/${target.id}`, { rows: gridToRows(grid) });
  } else {
    board.grid = grid;
    redraw();
    await api("/api/frame", { rows: gridToRows(grid) });
  }
}

function pause(on) {
  // while drawing straight on the board, don't let incoming frames overwrite the stroke
  if (on && !target && !paused) { pausePolling(true); paused = true; }
  if (!on && paused) { pausePolling(false); paused = false; }
}

function flood(x, y) {
  const target = G()[y][x], nv = valueFor(target);
  if (nv === target) return null;
  const g = cloneGrid(G()), stack = [[x, y]];
  while (stack.length) {
    const [cx, cy] = stack.pop();
    if (cx < 0 || cy < 0 || cx >= board.W || cy >= board.H || g[cy][cx] !== target) continue;
    g[cy][cx] = nv;
    stack.push([cx + 1, cy], [cx - 1, cy], [cx, cy + 1], [cx, cy - 1]);
  }
  return g;
}

// ---- pointer handling ----

canvas.addEventListener("pointerdown", safe(async e => {
  if (!active) return;
  const c = cellAt(e);
  canvas.setPointerCapture(e.pointerId);
  if (tool === "pencil") {
    pushHistory(); drawing = true; touched.clear(); lastPaint = null; pause(true);
    paintAt(c);
  } else if (tool === "fill") {
    const g = flood(c.x, c.y);
    if (g) { pushHistory(); await commit(g); }
  } else if (tool === "text") {
    if (!stampRows) return toast("Type some text first", true);
    pushHistory(); overlay = stampOverlay(stampRows, c); await commit(applyOverlay());
  } else if (tool === "select" && pasteMode) {
    pushHistory(); overlay = stampOverlay(clip, c, true); pasteMode = false; await commit(applyOverlay());
  } else {
    drawing = true; start = c; pause(true);
    if (tool === "select") selection = { x0: c.x, y0: c.y, x1: c.x, y1: c.y };
  }
  redraw();
}));

canvas.addEventListener("pointermove", e => {
  if (!active) return;
  const c = cellAt(e);
  hover = c.inside ? c : null;
  if (tool === "pencil" && drawing) paintAt(c);
  else if (drawing && tool === "select") selection = { ...selection, x1: c.x, y1: c.y };
  else if (drawing) overlay = overlayFrom(shapePts(tool, start, c), valueFor);
  else if (tool === "text" && stampRows && hover) overlay = stampOverlay(stampRows, c);
  else if (tool === "select" && pasteMode && hover) overlay = stampOverlay(clip, c, true);
  else overlay = null;
  redraw();
});

const endStroke = safe(async () => {
  if (!drawing) return;
  drawing = false;
  pause(false);
  if (tool === "pencil") return flushPending();
  if (tool === "select") return redraw();
  if (overlay) { pushHistory(); await commit(applyOverlay()); }
});
canvas.addEventListener("pointerup", endStroke);
canvas.addEventListener("pointercancel", endStroke);
canvas.addEventListener("pointerleave", () => {
  hover = null;
  if (!drawing) { overlay = null; redraw(); }
});

let lastPaint = null;
function paintAt(c) {
  // fill in the gap since the last pointer event so fast strokes stay solid
  const pts = lastPaint ? linePts(lastPaint.x, lastPaint.y, c.x, c.y) : [[c.x, c.y]];
  lastPaint = c;
  for (const [x, y] of pts) {
    const key = x + "," + y;
    if (touched.has(key)) continue;
    touched.add(key);
    G()[y][x] = valueFor(G()[y][x]);
    pending.push({ x, y, color });
  }
}

async function flushPending() {
  if (!pending.length) return;
  const dots = pending; pending = [];
  if (target) {
    if (!showLayerOnly()) redraw();
    if (wsSend({ type: "dots", drawing: target.id, dots })) return;
    try { await api(`/api/drawings/${target.id}/dots`, { dots }); } catch (e) { toast(e.message, true); }
    return;
  }
  if (wsSend({ type: "dots", dots })) return;
  try { await api("/api/dots", { dots }); } catch (e) { toast(e.message, true); }
}
setInterval(flushPending, 50);

// ---- toolbar ----

function setTool(t) {
  tool = t;
  document.querySelectorAll("#drawTools button").forEach(b => b.classList.toggle("active", b.dataset.tool === t));
  $("textOpts").hidden = t !== "text";
  $("selectOpts").hidden = t !== "select";
  if (t !== "select") { selection = null; pasteMode = false; }
  overlay = null;
  redraw();
}
$("drawTools").addEventListener("click", e => { const b = e.target.closest("button"); if (b) setTool(b.dataset.tool); });
$("drawColor").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  color = b.dataset.color;
  document.querySelectorAll("#drawColor button").forEach(x => x.classList.toggle("active", x === b));
});

async function undo() {
  if (!undoStack.length) return toast("Nothing to undo");
  redoStack.push(cloneGrid(G()));
  await commit(undoStack.pop());
}
async function redo() {
  if (!redoStack.length) return toast("Nothing to redo");
  undoStack.push(cloneGrid(G()));
  await commit(redoStack.pop());
}
$("undo").onclick = safe(undo);
$("redo").onclick = safe(redo);
const filled = v => G().map(r => r.map(() => v));
$("allBlack").onclick = safe(async () => { pushHistory(); target ? await commit(filled(false)) : await api("/api/fill", { color: "black" }); });
$("allWhite").onclick = safe(async () => { pushHistory(); target ? await commit(filled(true)) : await api("/api/fill", { color: "white" }); });
$("invert").onclick = safe(async () => { pushHistory(); target ? await commit(G().map(r => r.map(v => !v))) : await api("/api/invert", {}); });

$("saveScene").onclick = safe(async () => {
  const name = prompt("Scene name?", "My drawing");
  if (!name) return;
  await api("/api/scenes", { name, frames: [{ rows: gridToRows(G()), delay: 100 }] });
  await refreshScenes();
  toast(`Saved "${name}"`);
});

$("exportPng").onclick = () => {
  const c = document.createElement("canvas");
  c.width = board.W; c.height = board.H;
  const cx = c.getContext("2d"), img = cx.createImageData(board.W, board.H);
  G().forEach((row, y) => row.forEach((v, x) => {
    const i = (y * board.W + x) * 4;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v ? 255 : 0; img.data[i + 3] = 255;
  }));
  cx.putImageData(img, 0, 0);
  c.toBlob(b => download("flipdot.png", b));
};
$("exportPretty").onclick = () => {
  const c = document.createElement("canvas");
  c.width = board.W * 12; c.height = board.H * 12;
  renderDots(c.getContext("2d"), G(), 12, { seams: false });
  c.toBlob(b => download("flipdot-board.png", b));
};
$("importImg").onclick = () => { bus.dispatchEvent(new CustomEvent("tab", { detail: "media" })); $("file").click(); };

// text stamp
let stampFont = "normal";
async function updateStamp() {
  const text = $("stampText").value;
  const pixel = fontCache.list.find(f => f.id === stampFont)?.kind === "pixel";
  $("stampScaleBox").hidden = !pixel;
  $("stampSizeBox").hidden = pixel;
  stampRows = text ? (await api("/api/render_text", {
    text, font: stampFont, scale: +$("stampScale").value || 1, font_size: +$("stampSize").value || 14,
  })).rows : null;
}
function renderStampFont() {
  const sel = fontSelect(stampFont);
  sel.onchange = safe(() => { stampFont = sel.value; return updateStamp(); });
  $("stampFontBox").replaceChildren(sel);
}
bus.addEventListener("fonts", () => { renderStampFont(); updateStamp().catch(() => {}); });
$("stampText").addEventListener("input", safe(updateStamp));
$("stampScale").addEventListener("input", safe(updateStamp));
$("stampSize").addEventListener("input", safe(updateStamp));

// selection / clipboard
function copySel(cut) {
  if (!selection) return toast("Drag on the board to select first");
  const { x0, y0, x1, y1 } = normSel(selection);
  clip = [];
  for (let y = y0; y <= y1; y++) clip.push(G()[y].slice(x0, x1 + 1));
  toast(`${cut ? "Cut" : "Copied"} ${x1 - x0 + 1}×${y1 - y0 + 1}`);
  if (cut) {
    pushHistory();
    const g = cloneGrid(G());
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) g[y][x] = false;
    return commit(g);
  }
}
function paste() {
  if (!clip) return toast("Copy something first");
  setTool("select");
  pasteMode = true;
  toast("Click the board to place it (Esc to cancel)");
}
function flipSel(horizontal) {
  if (!selection) return toast("Select an area first");
  const { x0, y0, x1, y1 } = normSel(selection);
  pushHistory();
  const g = cloneGrid(G());
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++)
    g[y][x] = horizontal ? G()[y][x1 - (x - x0)] : G()[y1 - (y - y0)][x];
  return commit(g);
}
$("selCopy").onclick = safe(() => copySel(false));
$("selCut").onclick = safe(() => copySel(true));
$("selPaste").onclick = paste;
$("selFlipH").onclick = safe(() => flipSel(true));
$("selFlipV").onclick = safe(() => flipSel(false));

document.addEventListener("keydown", safe(async e => {
  if (!active || e.target.closest("input, textarea, select")) return;
  const k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
  if (mod && k === "z" && !e.shiftKey) { e.preventDefault(); await undo(); }
  else if (mod && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); await redo(); }
  else if (mod && k === "c" && selection) { e.preventDefault(); copySel(false); }
  else if (mod && k === "x" && selection) { e.preventDefault(); await copySel(true); }
  else if (mod && k === "v" && clip) { e.preventDefault(); paste(); }
  else if (k === "escape") { pasteMode = false; selection = null; overlay = null; redraw(); }
  else if (!mod && board.runner && !board.runner.interactive) {
    const t = { p: "pencil", l: "line", r: "rect", c: "circle", f: "fill", t: "text", s: "select" }[k];
    if (t) setTool(t);
  }
}));

// ---- what to draw on: the board, or a drawing layer ----

function renderTargets() {
  const sel = $("drawTarget");
  sel.replaceChildren(
    el("option", { value: "", selected: !target }, "The board (stops what's running)"),
    ...drawingsCache.list.map(d => el("option", { value: d.id, selected: target?.id === d.id }, `Layer: ${d.name}`)));
  updateTargetUi();
}

function updateTargetUi() {
  const isLayer = !!target;
  for (const id of ["layerOnlyBox", "renameDrawing", "deleteDrawing"]) $(id).hidden = !isLayer;
  $("showDrawing").hidden = !isLayer || onBoard();
  $("targetHint").textContent = !isLayer ? ""
    : onBoard() ? "On the board — what's running keeps going while you draw."
    : "Not on the board right now (showing just this layer).";
}

export async function setTarget(id) {
  if (!id) {
    target = null;
  } else {
    const d = await api(`/api/drawings/${id}`);
    target = { id: d.id, name: d.name, grid: rowsToGrid(d.rows) };
    if (!drawingsCache.list.some(x => x.id === id)) await loadDrawings();  // made elsewhere
  }
  undoStack.length = redoStack.length = 0;
  selection = null; overlay = null;
  renderTargets();
  redraw();
}

$("drawTarget").onchange = safe(() => setTarget($("drawTarget").value));
$("layerOnly").onchange = () => redraw();
$("newDrawing").onclick = safe(async () => {
  const j = await api("/api/drawings", { show: true });
  await loadDrawings();
  await setTarget(j.id);
  toast("New drawing layer added on top — draw away");
});
$("showDrawing").onclick = safe(async () => { await api(`/api/drawings/${target.id}/show`, {}); });
$("renameDrawing").onclick = safe(async () => {
  const name = prompt("Name for this drawing?", target.name);
  if (!name) return;
  await api(`/api/drawings/${target.id}`, { name });
  target.name = name;
  await loadDrawings();
});
$("deleteDrawing").onclick = safe(async () => {
  if (!confirm(`Delete drawing "${target.name}"? Layers using it will stop showing it.`)) return;
  await api(`/api/drawings/${target.id}/delete`, {});
  target = null;
  await loadDrawings();
  redraw();
});
bus.addEventListener("drawings", renderTargets);
bus.addEventListener("state", updateTargetUi);
bus.addEventListener("drawTarget", e => { bus.dispatchEvent(new CustomEvent("tab", { detail: "draw" })); setTarget(e.detail).catch(err => toast(err.message, true)); });
