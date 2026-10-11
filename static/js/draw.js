// Board view + Draw tab: drawing tools, undo/redo, selection, text stamp, export, animation frames, AI drawing.
// You can draw on the board itself (only with "Draw on the board" ticked: it stops whatever is running), on a drawing layer
// (which keeps the running layers going), or, in animation mode, on frames kept in the page.
import {
  $, api, board, bus, debounce, renderDots, cloneGrid, gridToRows, blankGrid, pausePolling, rowsToGrid,
  safe, toast, download, refreshScenes, wsSend, fontSelect, fontCache, drawingsCache, loadDrawings, el, scenesCache, confirmDelete,
  ask, confirmDialog,
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
let undoStack = [], redoStack = [];  // point at the current frame's history in animation mode
let target = null;               // null = the board, else { id, name, grid } of a drawing layer
let paused = false;
let anim = null;                 // { frames: [{ grid, delay, undo, redo }], cur, sceneId, name, dirty }
let animOn = false;
const mainHistory = { undo: undoStack, redo: redoStack };

// the grid the tools work on
const frame = () => anim.frames[anim.cur];
const G = () => (animOn ? frame().grid : target ? target.grid : board.grid);
const editingAnim = () => animOn && active;  // the canvas shows the frame being edited, not the board
const onBoard = () => !target || (board.runner?.drawings || []).includes(target.id);
const showLayerOnly = () => target && ($("layerOnly").checked || !onBoard());

// Strokes only land when the target was chosen on purpose: a frame, a drawing layer, or the board with
// "Draw on the board" ticked (off on every page load, since it stops whatever is running).
const takesStrokes = () => active && (animOn || !!target || $("drawLive").checked);
function updateCanvasMode() {
  const on = takesStrokes();
  canvas.classList.toggle("drawable", on || (active && zoom > 1));  // zoomed in: fingers pan and pinch here, not the page
  canvas.style.cursor = panning ? "grab" : on ? "crosshair" : "default";
}
export function setActive(on) {
  active = on;
  if (!on && zoom > 1) setZoom(1);  // the same canvas is the big preview on Now playing
  updateCanvasMode();
  redraw();  // the board may have moved on while the canvas was hidden (main.js shows #device before this)
}

function redraw() {
  // the big canvas is only on Now playing and Draw: on every other page (and in a background tab) skip the 3528
  // cells per board frame, and catch up when it comes back (setActive / the tab's "state" event)
  if (!board.grid.length || $("device").hidden || document.hidden) return;
  if (canvas.width !== board.W * CELL) { canvas.width = board.W * CELL; canvas.height = board.H * CELL; }
  renderDots(ctx, editingAnim() ? frame().grid : showLayerOnly() ? target.grid : board.grid, CELL, { overlay });
  if (!active) return;
  if (animOn && $("animOnion").checked && anim.cur > 0) drawOnion(anim.frames[anim.cur - 1].grid, frame().grid);
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
document.addEventListener("visibilitychange", () => { if (!document.hidden) redraw(); });

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
  if (animOn) {
    frame().grid = grid;
    redraw();
    frameChanged();
  } else if (target) {
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
  if (on && !target && !animOn && !paused) { pausePolling(true); paused = true; }
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

// ---- zoom and pan (dots are small on a phone): buttons, two-finger pinch, or the Move tool ----

const wrap = $("boardWrap");
let zoom = 1, panning = false;
const pointers = new Map();  // pointerId -> {x, y}
let gesture = null;          // two-finger pinch: { dist, zoom, mx, my }
let drag = null;             // Move tool: last pointer position

function setZoom(z, cx = null, cy = null) {
  z = Math.max(1, Math.min(4, Math.round(z * 4) / 4));
  const r = wrap.getBoundingClientRect();
  // keep the point under the fingers (or the middle) where it was
  const px = cx ?? r.left + r.width / 2, py = cy ?? r.top + r.height / 2;
  const fx = (wrap.scrollLeft + px - r.left) / (canvas.offsetWidth || 1), fy = (wrap.scrollTop + py - r.top) / (canvas.offsetHeight || 1);
  zoom = z;
  canvas.style.width = `${zoom * 100}%`;
  wrap.classList.toggle("zoomed", zoom > 1);
  wrap.scrollLeft = fx * canvas.offsetWidth - (px - r.left);
  wrap.scrollTop = fy * canvas.offsetHeight - (py - r.top);
  $("zoomLevel").textContent = `${zoom}×`;
  $("zoomOut").disabled = zoom <= 1;
  $("zoomIn").disabled = zoom >= 4;
  $("panMode").hidden = zoom <= 1;
  if (zoom <= 1) setPanning(false);
}
function setPanning(on) {
  panning = on;
  $("panMode").setAttribute("aria-pressed", String(on));
  canvas.classList.toggle("panning", on);
  updateCanvasMode();
}
$("zoomIn").onclick = () => setZoom(zoom + 1);
$("zoomOut").onclick = () => setZoom(zoom - 1);
$("panMode").onclick = () => setPanning(!panning);
setZoom(1);

// registered before the drawing handlers, so a pinch or a Move drag never reaches them
canvas.addEventListener("pointerdown", e => {
  if (!active) return;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (pointers.size === 2 && e.pointerType === "touch") {
    // second finger: this is a pinch, not a stroke
    drawing = false; overlay = null; pause(false);
    const [a, b] = [...pointers.values()];
    gesture = { dist: Math.hypot(a.x - b.x, a.y - b.y), zoom, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 };
    e.stopImmediatePropagation();
  } else if (gesture || ((panning || !takesStrokes()) && zoom > 1)) {
    if (!gesture) { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId); }
    e.stopImmediatePropagation();
  }
});
canvas.addEventListener("pointermove", e => {
  if (!pointers.has(e.pointerId)) { if (gesture || drag) e.stopImmediatePropagation(); return; }
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (gesture && pointers.size >= 2) {
    const [a, b] = [...pointers.values()];
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    wrap.scrollLeft -= mx - gesture.mx; wrap.scrollTop -= my - gesture.my;
    gesture.mx = mx; gesture.my = my;
    const z = gesture.zoom * Math.hypot(a.x - b.x, a.y - b.y) / (gesture.dist || 1);
    if (Math.abs(z - zoom) >= 0.25) setZoom(z, mx, my);
    e.stopImmediatePropagation();
  } else if (drag) {
    wrap.scrollLeft -= e.clientX - drag.x; wrap.scrollTop -= e.clientY - drag.y;
    drag = { x: e.clientX, y: e.clientY };
    e.stopImmediatePropagation();
  } else if (gesture) e.stopImmediatePropagation();
});
const pointerGone = e => {
  pointers.delete(e.pointerId);
  if (gesture || drag) {
    e.stopImmediatePropagation();
    if (!pointers.size) { gesture = null; drag = null; }
  }
};
canvas.addEventListener("pointerup", pointerGone);
canvas.addEventListener("pointercancel", pointerGone);

// ---- pointer handling ----

canvas.addEventListener("pointerdown", safe(async e => {
  if (!active) return;
  if (!takesStrokes()) {
    if (e.pointerType === "mouse") toast("To draw, tick \"Draw on the board\" (that stops what's running) or start a drawing layer.");
    return;
  }
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
  if (!takesStrokes()) { if (hover) { hover = null; overlay = null; redraw(); } return; }
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
  if (animOn) return frameChanged();  // frames live in the page until saved
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

// the board only changes from here with "Draw on the board" ticked (frames and drawing layers are always fine)
const boardWritable = () => animOn || !!target || $("drawLive").checked;

async function undo() {
  if (!undoStack.length) return toast("Nothing to undo");
  if (!boardWritable()) return toast("Tick \"Draw on the board\" to undo on the board (that stops what's running).", false, null, 5000);
  redoStack.push(cloneGrid(G()));
  await commit(undoStack.pop());
}
async function redo() {
  if (!redoStack.length) return toast("Nothing to redo");
  if (!boardWritable()) return toast("Tick \"Draw on the board\" to redo on the board (that stops what's running).", false, null, 5000);
  undoStack.push(cloneGrid(G()));
  await commit(redoStack.pop());
}
$("undo").onclick = safe(undo);
$("redo").onclick = safe(redo);
const filled = v => G().map(r => r.map(() => v));
const local = () => target || animOn;
const boardOk = () => local() || $("drawLive").checked || (toast("Tick \"Draw on the board\" first: changing the board stops what's running.", false, null, 5000), false);
$("allBlack").onclick = safe(async () => { if (!boardOk()) return; pushHistory(); local() ? await commit(filled(false)) : await api("/api/fill", { color: "black" }); });
$("allWhite").onclick = safe(async () => { if (!boardOk()) return; pushHistory(); local() ? await commit(filled(true)) : await api("/api/fill", { color: "white" }); });
$("invert").onclick = safe(async () => { if (!boardOk()) return; pushHistory(); local() ? await commit(G().map(r => r.map(v => !v))) : await api("/api/invert", {}); });

$("saveScene").onclick = safe(async () => {
  if (animOn) return saveAnim(true);
  const name = await ask({ title: "Save to Pictures", label: "Name", value: "My drawing", confirmText: "Save" });
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
$("exportGif").onclick = safe(async () => {
  const box = $("gifOpts");
  box.hidden = !box.hidden;
  if (box.hidden) return;
  const j = await api("/api/timelapse");
  $("gifInfo").textContent = `${Math.floor(j.seconds / 60)} min ${j.seconds % 60} s recorded (${j.frames} changes)`;
});
$("gifDownload").onclick = () => {
  const q = new URLSearchParams({ minutes: $("gifMinutes").value, speed: $("gifSpeed").value, scale: $("gifScale").value });
  const a = el("a", { href: `/api/timelapse.gif?${q}`, download: "flipdot-timelapse.gif" });
  document.body.append(a);
  a.click();
  a.remove();
  toast("Rendering GIF…");
};
$("importImg").onclick = () => { bus.dispatchEvent(new CustomEvent("tab", { detail: "import" })); $("file").click(); };

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
const stampSoon = debounce(safe(updateStamp));  // one render per pause in typing, not one per keystroke
$("stampText").addEventListener("input", stampSoon);
$("stampScale").addEventListener("input", stampSoon);
$("stampSize").addEventListener("input", stampSoon);

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
  else if (animOn && !mod && (k === "," || k === ".")) selectFrame(anim.cur + (k === "," ? -1 : 1));
  else if (!mod && board.runner && !board.runner.interactive) {
    const t = { p: "pencil", l: "line", r: "rect", c: "circle", f: "fill", t: "text", s: "select" }[k];
    if (t) setTool(t);
  }
}));

// ---- what to draw on: the board, or a drawing layer ----

function renderTargets() {
  const sel = $("drawTarget");
  sel.replaceChildren(
    el("option", { value: "", selected: !target }, "The board"),
    ...drawingsCache.list.map(d => el("option", { value: d.id, selected: target?.id === d.id }, `Layer: ${d.name}`)));
  updateTargetUi();
}

function updateTargetUi() {
  const isLayer = !!target;
  $("drawLiveBox").hidden = isLayer || animOn;
  updateCanvasMode();
  for (const id of ["layerOnlyBox", "renameDrawing", "deleteDrawing"]) $(id).hidden = !isLayer;
  $("showDrawing").hidden = !isLayer || onBoard();
  $("targetHint").textContent = !isLayer ? (animOn ? "" : $("drawLive").checked
      ? "Strokes go straight to the board and stop whatever is running. Press Resume schedule at the top to go back."
      : "View only. Tick Draw on the board to draw on it (that stops what's running), or start a drawing layer to draw on top.")
    : onBoard() ? "On the board. What's running keeps going while you draw."
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
$("drawLive").onchange = () => { updateTargetUi(); redraw(); };
$("newDrawing").onclick = safe(async () => {
  const j = await api("/api/drawings", { show: true });
  await loadDrawings();
  await setTarget(j.id);
  toast("New drawing layer added on top");
});
$("showDrawing").onclick = safe(async () => { await api(`/api/drawings/${target.id}/show`, {}); });
$("renameDrawing").onclick = safe(async () => {
  const name = await ask({ title: "Rename drawing", label: "Name", value: target.name, confirmText: "Rename" });
  if (!name || name === target.name) return;
  await api(`/api/drawings/${target.id}`, { name });
  target.name = name;
  await loadDrawings();
});
$("deleteDrawing").onclick = safe(async () => {
  if (!await confirmDelete("drawing", target.id, `the drawing "${target.name}"`)) return;
  await api(`/api/drawings/${target.id}/delete`, {});
  bus.dispatchEvent(new Event("refs"));
  target = null;
  await loadDrawings();
  redraw();
});
bus.addEventListener("drawings", renderTargets);
bus.addEventListener("state", updateTargetUi);
bus.addEventListener("drawTarget", e => { bus.dispatchEvent(new CustomEvent("tab", { detail: "draw" })); setTarget(e.detail).catch(err => toast(err.message, true)); });

// ---- animation mode: frames live in the page until saved to Pictures ----

const MIN_DELAY = 20, MAX_DELAY = 60000;
const clampDelay = v => Math.max(MIN_DELAY, Math.min(MAX_DELAY, Math.round(+v) || defaultDelay()));
const defaultDelay = () => Math.max(MIN_DELAY, Math.min(MAX_DELAY, Math.round(+$("animDefault").value) || 200));
const newFrame = (grid, delay = defaultDelay()) => ({ grid, delay, undo: [], redo: [] });

function drawOnion(prev, cur) {
  // dots lit in the previous frame but dark in this one, as faint rings (only on this canvas, never on the board)
  ctx.strokeStyle = "rgba(233, 185, 73, 0.55)";
  ctx.lineWidth = 2;
  for (let y = 0; y < prev.length; y++)
    for (let x = 0; x < prev[y].length; x++)
      if (prev[y][x] && !cur[y]?.[x]) {
        ctx.beginPath(); ctx.arc(x * CELL + CELL / 2, y * CELL + CELL / 2, CELL * 0.3, 0, Math.PI * 2); ctx.stroke();
      }
}

function useHistory(h) {
  undoStack = h.undo; redoStack = h.redo;
}

function thumb(f) {
  if (f.thumb) return f.thumb;  // cached until the frame changes
  const c = f.thumb = document.createElement("canvas");
  c.width = board.W * 3; c.height = board.H * 3;
  renderDots(c.getContext("2d"), f.grid, 3, { seams: false });
  return c;
}

function renderStrip() {
  $("animStrip").replaceChildren(...anim.frames.map((f, i) => {
    const b = el("button", { class: "fr" + (i === anim.cur ? " active" : ""), title: `Frame ${i + 1}`, "aria-pressed": String(i === anim.cur),
                             "aria-label": `Frame ${i + 1}, ${f.delay} ms`, onclick: () => selectFrame(i) },
      thumb(f), el("span", {}, `${i + 1} · ${f.delay} ms`));
    return b;
  }));
  $("animStrip").children[anim.cur]?.scrollIntoView({ block: "nearest", inline: "nearest" });
  $("animDelay").value = frame().delay;
  const total = anim.frames.reduce((t, f) => t + f.delay, 0);
  $("animInfo").textContent = `${anim.name}${anim.dirty ? " (unsaved)" : ""} · ${anim.frames.length} frame${anim.frames.length === 1 ? "" : "s"} · ${(total / 1000).toFixed(1)} s`;
  $("animDel").disabled = anim.frames.length < 2;
  $("animLeft").disabled = anim.cur === 0;
  $("animRight").disabled = anim.cur === anim.frames.length - 1;
}

let thumbTimer = null;
function frameChanged() {
  if (!anim.dirty) { anim.dirty = true; frame().thumb = null; renderStrip(); return; }
  const f = frame(), i = anim.cur;
  f.thumb = null;
  clearTimeout(thumbTimer);  // just refresh this frame's thumbnail, at most every 150 ms
  thumbTimer = setTimeout(() => {
    const slot = $("animStrip").children[i];
    if (slot && anim.frames[i] === f) slot.replaceChild(thumb(f), slot.firstChild);
  }, 150);
}

function selectFrame(i) {
  if (!anim || i < 0 || i >= anim.frames.length) return;
  anim.cur = i;
  useHistory(frame());
  selection = null; overlay = null; pasteMode = false;
  renderStrip();
  redraw();
}

function setAnim(frames, { name = "Animation", sceneId = null, dirty = false } = {}) {
  anim = { frames, cur: 0, name, sceneId, dirty };
  animOn = true;
  $("animMode").checked = true;
  $("animPanel").hidden = false;
  updateTargetUi();
  selectFrame(0);
}

function setAnimMode(on) {
  if (on && !anim) setAnim([newFrame(cloneGrid(G()))]);  // start from what's on the canvas now
  animOn = on;
  $("animMode").checked = on;
  $("animPanel").hidden = !on;
  updateTargetUi();
  if (on) selectFrame(anim.cur);
  else { useHistory(mainHistory); selection = null; overlay = null; redraw(); }
}
$("animMode").onchange = () => setAnimMode($("animMode").checked);

function edit(fn) {
  fn();
  anim.dirty = true;
  selectFrame(Math.max(0, Math.min(anim.cur, anim.frames.length - 1)));
}
$("animAdd").onclick = () => edit(() => { anim.frames.splice(anim.cur + 1, 0, newFrame(blankGrid())); anim.cur++; });
$("animDup").onclick = () => edit(() => { anim.frames.splice(anim.cur + 1, 0, newFrame(cloneGrid(frame().grid), frame().delay)); anim.cur++; });
$("animDel").onclick = () => edit(() => { if (anim.frames.length > 1) anim.frames.splice(anim.cur, 1); });
const move = d => edit(() => {
  const j = anim.cur + d;
  if (j < 0 || j >= anim.frames.length) return;
  [anim.frames[anim.cur], anim.frames[j]] = [anim.frames[j], anim.frames[anim.cur]];
  anim.cur = j;
});
$("animLeft").onclick = () => move(-1);
$("animRight").onclick = () => move(1);
$("animDelay").onchange = () => edit(() => { frame().delay = clampDelay($("animDelay").value); });
$("animAllDelay").onclick = () => edit(() => { const d = defaultDelay(); anim.frames.forEach(f => (f.delay = d)); });
$("animOnion").onchange = () => redraw();
$("animNew").onclick = safe(async () => {
  if (anim?.dirty && !await confirmDialog({ title: "Start a new animation?", message: "Unsaved changes to this one are lost.", confirmText: "Start new", danger: true })) return;
  setAnim([newFrame(blankGrid())]);
});

const payload = () => anim.frames.map(f => ({ rows: gridToRows(f.grid), delay: f.delay }));
$("animPlay").onclick = safe(async () => {
  await api("/api/animation", { frames: payload(), loop: true, name: anim.name });
  toast("Showing it on the board");
});
$("animStop").onclick = safe(() => api("/api/animation/stop", {}));

async function saveAnim(asNew) {
  let name = anim.name;
  if (asNew || !anim.sceneId) {
    name = await ask({ title: asNew ? "Save to Pictures as a new animation" : "Save animation", label: "Name", value: anim.sceneId ? `${anim.name} copy` : anim.name, confirmText: "Save" });
    if (!name) return;
  }
  const j = await api("/api/scenes", { name, frames: payload(), id: asNew ? undefined : anim.sceneId || undefined });
  Object.assign(anim, { name, sceneId: j.id, dirty: false });
  renderStrip();
  await refreshScenes();
  toast(`Saved "${name}"`);
}
$("animSave").onclick = safe(() => saveAnim(false));

function renderSceneChoices() {
  $("animScene").replaceChildren(...scenesCache.list.length
    ? scenesCache.list.map(s => el("option", { value: s.id }, s.frames > 1 ? `${s.name} (${s.frames} frames)` : s.name))
    : [el("option", { value: "" }, "Nothing saved in Pictures yet")]);
}
bus.addEventListener("scenes", renderSceneChoices);

export async function openScene(id) {
  if (!id) return;
  if (anim?.dirty && !await confirmDialog({ title: "Open another animation?", message: "Unsaved changes to this animation are lost.", confirmText: "Open", danger: true })) return;
  const s = await api(`/api/scenes/${id}`);
  setAnim(s.frames.map(f => newFrame(rowsToGrid(f.rows), clampDelay(f.delay ?? 100))), { name: s.name, sceneId: id });
  bus.dispatchEvent(new CustomEvent("tab", { detail: "draw" }));
}
$("animOpen").onclick = safe(() => openScene($("animScene").value));
bus.addEventListener("editScene", e => openScene(e.detail).catch(err => toast(err.message, true)));

// ---- "Draw me…": Claude draws it (POST /api/ai/draw) ----

$("aiToggle").setAttribute("aria-expanded", "false");
$("aiToggle").onclick = () => {
  $("aiOpts").hidden = !$("aiOpts").hidden;
  $("aiToggle").setAttribute("aria-expanded", String(!$("aiOpts").hidden));
  $("aiToggle").classList.toggle("active", !$("aiOpts").hidden);
  if (!$("aiOpts").hidden) $("aiPrompt").focus();
};
$("aiPrompt").addEventListener("keydown", e => { if (e.key === "Enter") $("aiGo").click(); });
$("aiGo").onclick = safe(async () => {
  const text = $("aiPrompt").value.trim();
  if (!text) return toast("Describe what to draw first", true);
  const frames = Math.max(1, Math.min(8, Math.round(+$("aiFrames").value) || 1));
  $("aiGo").disabled = true;
  $("aiStatus").textContent = frames > 1 ? `Drawing ${frames} frames… this can take a minute or two.` : "Drawing… this can take a minute.";
  let j;
  try {
    j = await api("/api/ai/draw", { prompt: text, frames, style: $("aiStyle").value.trim() });
  } catch (e) {
    $("aiStatus").textContent = "";
    throw e;
  } finally {
    $("aiGo").disabled = false;
  }
  const grids = j.frames.map(f => rowsToGrid(f.rows));
  const note = [j.frames.length < frames ? `got ${j.frames.length} of ${frames} frames` : "",
                j.repaired ? `${j.repaired} resized to fit` : ""].filter(Boolean).join(", ");
  $("aiStatus").textContent = `Done${note ? ` (${note})` : ""}. Undo puts back what was there.`;
  if (grids.length > 1) {
    if (anim?.dirty && !await confirmDialog({ title: "Replace the animation you're editing?", message: "Unsaved changes to it are lost.", confirmText: "Replace", danger: true })) return;
    const d = defaultDelay();
    setAnim(grids.map(g => newFrame(g, d)), { name: text.slice(0, 40), dirty: true });
  } else if (boardWritable()) {
    pushHistory();
    await commit(grids[0]);  // current frame in animation mode, else the board or drawing layer
  } else {
    // "Draw on the board" is off: keep it off the board, on a new drawing layer you can add when you like
    const d = await api("/api/drawings", { name: text.slice(0, 40), rows: gridToRows(grids[0]) });
    await loadDrawings();
    await setTarget(d.id);
    $("aiStatus").textContent = "Done. It's on a new drawing layer, not on the board yet: press Add as layer to show it.";
  }
});
