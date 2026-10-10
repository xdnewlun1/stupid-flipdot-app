import { $, api, board, bus, live, loadCatalog, loadDrawings, loadFonts, refreshScenes, safe, startPolling, toast, wsSend } from "./common.js";
import { setActive as setDrawActive } from "./draw.js";
import "./media.js";
import { loadGalleries } from "./slideshows.js";
import { renderAppList } from "./apps.js";
import { loadPlaylists } from "./playlists.js";
import { loadCompositions, renderGallery } from "./layers.js";
import "./scenes.js";
import { loadSettings } from "./settings.js";

const TABS = ["draw", "media", "apps", "layers", "playlists", "scenes", "settings"];

function showTab(name) {
  if (!TABS.includes(name)) name = "draw";
  for (const t of TABS) $(`tab-${t}`).hidden = t !== name;
  $("tab-media-slides").hidden = name !== "media";
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  setDrawActive(name === "draw");
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  if (name === "settings") loadSettings().catch(e => toast(e.message, true));
}
$("tabs").addEventListener("click", e => { const b = e.target.closest("button"); if (b) showTab(b.dataset.tab); });
bus.addEventListener("tab", e => showTab(e.detail));

// ---- status line ----
function renderMode() {
  const r = board.runner;
  if (!r) return;
  const what = r.description ? `<b>${escapeHtml(r.description)}</b>` : "";
  let text;
  if (r.notification) text = `Notification: ${what}`;
  else if (r.mode === "manual") text = "<b>Manual</b> — drawing mode" + (r.schedule_target ? " (schedule paused)" : "");
  else if (r.mode === "app") text = `Running ${what}`;
  else if (r.mode === "playlist") text = `Playlist <b>${escapeHtml(r.playlist)}</b> → ${what}`;
  else text = r.playlist === "__off__" ? "Schedule: <b>off</b>" : r.playlist ? `Schedule: <b>${escapeHtml(r.playlist)}</b> → ${what}` : "Schedule: nothing scheduled";
  if (r.interactive) text += ` · <a href="/play" target="_blank">controller</a>`;
  text += live() ? ` <span class="live" title="Live updates over WebSocket">● live</span>` : ` <span class="muted" title="WebSocket down; polling instead">○ polling</span>`;
  $("mode").innerHTML = text;
  $("btnNext").disabled = !(r.mode === "playlist" || r.mode === "schedule");
  $("btnStop").disabled = r.mode === "manual" && !r.notification;
}
const escapeHtml = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
bus.addEventListener("state", renderMode);
bus.addEventListener("offline", () => ($("mode").textContent = "server unreachable — retrying…"));
bus.addEventListener("live", renderMode);
bus.addEventListener("reconnecting", renderMode);

$("btnStop").onclick = safe(() => api("/api/stop", {}));
$("btnNext").onclick = safe(() => api("/api/next", {}));
$("btnSchedule").onclick = safe(async () => { await api("/api/schedule/resume", {}); toast("Following the schedule"); });

// ---- keyboard control for games ----
const KEYS = {
  ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
  w: "up", s: "down", a: "left", d: "right", " ": "a", Enter: "b", x: "a", z: "b",
};
function gameKey(e, action) {
  if (!board.runner?.interactive || e.target.closest("input, textarea, select") || e.ctrlKey || e.metaKey) return;
  const key = KEYS[e.key] || KEYS[e.key.toLowerCase?.()];
  if (!key) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  if (e.repeat) return;
  const msg = { key, action, player: 1 };
  if (!wsSend({ type: "input", ...msg }))
    fetch("/api/input", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(msg) });
}
window.addEventListener("keydown", e => gameKey(e, "down"), true);
window.addEventListener("keyup", e => gameKey(e, "up"), true);

// ---- start ----
(async () => {
  showTab(location.hash.slice(1));
  startPolling();
  try {
    await loadCatalog();
    await loadFonts();
    await loadDrawings();
    await loadGalleries();
    await refreshScenes();
    await loadCompositions();
    renderGallery();
    renderAppList();
    await loadPlaylists();
  } catch (e) {
    toast(e.message, true);
  }
})();
