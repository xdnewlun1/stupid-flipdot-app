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
  document.querySelectorAll("#tabs button").forEach(b => {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    if (on) { b.setAttribute("aria-current", "page"); $("pageTitle").textContent = b.textContent.trim(); }
    else b.removeAttribute("aria-current");
  });
  setDrawActive(name === "draw");
  document.body.dataset.tab = name;
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
  const dot = live() ? `<span class="dot live" title="Live updates over WebSocket"></span>` : `<span class="dot poll" title="WebSocket down; polling instead"></span>`;
  $("mode").innerHTML = dot + `<span>${text}</span>`;
  $("deviceState").textContent = live() ? "live" : "polling";
  $("sideFoot").textContent = live() ? "connected · live" : "reconnecting…";
  $("btnNext").disabled = !(r.mode === "playlist" || r.mode === "schedule");
  $("btnStop").disabled = r.mode === "manual" && !r.notification;
}
const escapeHtml = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
bus.addEventListener("state", renderMode);
bus.addEventListener("offline", () => {
  $("mode").innerHTML = `<span class="dot off"></span><span>Server unreachable — retrying…</span>`;
  $("deviceState").textContent = "offline";
  $("sideFoot").textContent = "offline";
});
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

// ---- theme: Auto (follow the OS) / Light / Dark, remembered per browser ----
function applyTheme(choice) {
  if (choice === "light" || choice === "dark") document.documentElement.dataset.theme = choice;
  else delete document.documentElement.dataset.theme;
  document.querySelectorAll("[data-theme-choice]").forEach(b => b.classList.toggle("active", b.dataset.themeChoice === (choice || "auto")));
}
let savedTheme = "auto";
try { savedTheme = localStorage.getItem("theme") || "auto"; } catch {}
applyTheme(savedTheme);
document.querySelectorAll("[data-theme-choice]").forEach(b => b.addEventListener("click", () => {
  applyTheme(b.dataset.themeChoice);
  try { localStorage.setItem("theme", b.dataset.themeChoice); } catch {}
}));

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
