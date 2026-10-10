// Settings tab: weather location, gamepads, quiet hours/board health/setup (boardcare.js), fonts, Music Assistant, MQTT, storage, webhook docs.
import { $, api, bus, el, fontCache, fontSelect, loadFonts, renderDots, rowsToGrid, safe, toast } from "./common.js";
import { loadBoardCare } from "./boardcare.js";
import { loadGuestWall } from "./guestwall.js";

let settings = {};

export async function loadSettings() {
  settings = await api("/api/settings");
  const wx = settings.weather || {};
  $("wxCurrent").textContent = wx.name ? `${wx.name} (${(+wx.lat).toFixed(2)}, ${(+wx.lon).toFixed(2)})` : "Not set. Search for a city above.";
  $("wxUnits").value = wx.units || "fahrenheit";
  $("trFps").value = settings.display?.transition_fps ?? 15;
  const sp = settings.sendspin || {};
  $("ssEnabled").checked = !!sp.enabled;
  $("ssName").value = sp.name || "Flipdot board";
  $("ssUnpaired").checked = !!sp.unpaired;
  $("ssServer").value = sp.server_url ?? "auto";
  refreshSendspin().catch(() => {});
  const ma = settings.music_assistant || {};
  $("maUrl").value = ma.url || "";
  $("maToken").value = ma.token || "";
  maPlayer = ma.player || "";
  if (ma.url) testMusic().catch(() => {});
  const mq = settings.mqtt || {};
  $("mqEnabled").checked = !!mq.enabled;
  $("mqHost").value = mq.host || "";
  $("mqPort").value = mq.port || 1883;
  $("mqUser").value = mq.username || "";
  $("mqPass").value = mq.password || "";
  $("mqPrefix").value = mq.prefix || "flipdot";
  $("mqDiscovery").checked = !!mq.discovery;
  $("mqDiscPrefix").value = mq.discovery_prefix || "homeassistant";
  $("mqStatus").textContent = !mq.enabled ? "Off" : mq.connected ? "Connected" : `Not connected${mq.error ? `: ${mq.error}` : ""}`;
  renderHooks(mq.prefix || "flipdot");
  const ai = settings.ai || {};
  $("aiKey").value = ai.api_key || "";
  aiModels().then(models => {
    $("aiModel").replaceChildren(...models.map(([v, label]) => el("option", { value: v, selected: v === (ai.model || models[0][0]) }, label)));
  }).catch(() => {});
  const gp = settings.gamepad || {};
  $("gpEnabled").checked = gp.enabled !== false;
  $("gpDeadzone").value = gp.deadzone ?? 0.35;
  $("gpDeadzoneVal").textContent = (+$("gpDeadzone").value).toFixed(2);
  gpMapping = { ...(gp.mapping || {}) };
  gpTableDrawn = false;
  refreshGamepad().catch(() => {});
  refreshStorage().catch(() => {});
  loadGuestWall().catch(() => {});
  await loadBoardCare(settings);
}

const save = async section => { settings = await api("/api/settings", section); await loadSettings(); toast("Saved"); };

$("wxFind").onclick = safe(async () => {
  const q = $("wxSearch").value.trim();
  if (!q) return;
  const { results } = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
  $("wxResults").replaceChildren(...(results.length ? results.map(r =>
    el("button", { class: "ghost", onclick: safe(async () => {
      await save({ weather: { name: r.name, lat: r.lat, lon: r.lon, units: $("wxUnits").value } });
      $("wxResults").replaceChildren();
    }) }, r.name)) : [el("span", { class: "muted" }, `No places match "${q}". Try a nearby larger city.`)]));
});
$("wxSearch").addEventListener("keydown", e => { if (e.key === "Enter") $("wxFind").click(); });
$("wxUnits").onchange = safe(() => save({ weather: { units: $("wxUnits").value } }));

let aiModelList = null;
const aiModels = async () => (aiModelList ||= (await api("/api/ai")).models);
$("aiSave").onclick = safe(() => save({ ai: { api_key: $("aiKey").value.trim(), model: $("aiModel").value } }));

let maPlayer = "";
async function testMusic() {
  $("maStatus").textContent = "Connecting…";
  try {
    const { players } = await api("/api/music/players");
    const sel = $("maPlayer");
    sel.replaceChildren(el("option", { value: "" }, "Whatever is playing"),
      ...players.map(p => el("option", { value: p.name, selected: p.name === maPlayer }, `${p.name}${p.state === "playing" ? " (playing)" : ""}`)));
    const now = players.find(p => p.state === "playing");
    $("maStatus").textContent = `Connected: ${players.length} player${players.length === 1 ? "" : "s"}` + (now ? `, playing "${now.title}" on ${now.name}` : "");
  } catch (e) {
    $("maStatus").textContent = "Not connected: " + e.message;
  }
}
async function refreshSendspin() {
  const j = await api("/api/sendspin");
  $("ssStatusLine").textContent = j.status + (j.receiving ? " · receiving visualizer data" : "")
    + (j.metadata?.title ? ` · now: ${j.metadata.title}` : "") + (j.version ? ` · aiosendspin ${j.version}` : "")
    + (j.warning ? ` · Warning: ${j.warning}` : "");
}
$("ssSave").onclick = safe(async () => {
  await save({ sendspin: { enabled: $("ssEnabled").checked, name: $("ssName").value.trim() || "Flipdot board",
                           unpaired: $("ssUnpaired").checked, server_url: $("ssServer").value.trim() } });
  toast("Saved. Restart the server if you changed Connect to.");
  setTimeout(() => refreshSendspin().catch(() => {}), 1500);
});
$("ssFind").onclick = safe(async () => {
  $("ssStatusLine").textContent = "Searching the network…";
  const { servers } = await api("/api/sendspin/discover");
  if (!servers.length) { $("ssStatusLine").textContent = "No Music Assistant Sendspin server found on the network. Enter its address by hand."; return; }
  $("ssServer").value = servers[0].url;
  $("ssStatusLine").textContent = `Found ${servers.map(x => x.url).join(", ")}. Press Save, then restart the server.`;
});
$("ssPair").onclick = safe(async () => {
  await api("/api/sendspin/pair", {});
  toast("Pairing window open. Finish pairing in Music Assistant; the board shows a code if one is needed.");
});
$("trFps").onchange = safe(() => save({ display: { transition_fps: Math.max(5, Math.min(30, +$("trFps").value || 15)) } }));
$("maSave").onclick = safe(async () => {
  await save({ music_assistant: { url: $("maUrl").value.trim(), token: $("maToken").value.trim(), player: $("maPlayer").value } });
});
$("maPlayer").onchange = safe(async () => {
  maPlayer = $("maPlayer").value;
  await api("/api/settings", { music_assistant: { player: maPlayer } });
  toast("Saved");
});

$("mqSave").onclick = safe(async () => {
  await save({ mqtt: {
    enabled: $("mqEnabled").checked, host: $("mqHost").value.trim(), port: +$("mqPort").value || 1883,
    username: $("mqUser").value, password: $("mqPass").value, prefix: $("mqPrefix").value.trim() || "flipdot",
    discovery: $("mqDiscovery").checked, discovery_prefix: $("mqDiscPrefix").value.trim() || "homeassistant",
  } });
  setTimeout(() => loadSettings().catch(() => {}), 2500);  // show connection result
});

function renderHooks(prefix) {
  const base = location.origin;
  const pre = text => el("pre", {}, text);
  $("hooks").replaceChildren(
    el("p", { class: "muted" }, "Anything that can make an HTTP request can put things on the board. A notification shows for a while, then the board goes back to what it was doing."),
    el("h3", {}, "Show a message"),
    pre(`curl -X POST ${base}/api/notify -H 'Content-Type: application/json' \\
  -d '{"text": "Dinner is ready!", "duration": 15}'

# or simply:  ${base}/api/notify?text=Hello&duration=10`),
    el("h3", {}, "Show an image (URL or base64), and optionally save it as a scene"),
    pre(`curl -X POST ${base}/api/notify -H 'Content-Type: application/json' \\
  -d '{"url": "https://example.com/pic.png", "mode": "fit", "dither": true, "duration": 20}'

curl -X POST '${base}/api/image?mode=crop&dither=true&save=My%20photo' --data-binary @photo.jpg`),
    el("h3", {}, "Run an app or control playback"),
    pre(`curl -X POST ${base}/api/run -H 'Content-Type: application/json' -d '{"app": "clock", "params": {"seconds": true}}'
curl -X POST ${base}/api/stop             # stop, keep picture
curl -X POST ${base}/api/schedule/resume  # back to the schedule
curl -X POST ${base}/api/next             # next playlist item
curl -X POST ${base}/api/playlist/play -H 'Content-Type: application/json' -d '{"name": "Default"}'`),
    el("h3", {}, "Home Assistant (configuration.yaml)"),
    pre(`rest_command:
  flipdot_notify:
    url: "${base}/api/notify"
    method: POST
    content_type: "application/json"
    payload: '{"text": "{{ message }}", "duration": {{ duration | default(15) }}}'

# then in an automation:
#   action: rest_command.flipdot_notify
#   data: { message: "Washer done" }`),
    el("h3", {}, "MQTT topics"),
    pre(`${prefix}/notify    text, or JSON like the /api/notify body
${prefix}/run       {"app": "weather"}
${prefix}/command   stop | schedule | next | black | white | invert
${prefix}/event     an event name for Automations, e.g. doorbell
${prefix}/status    (published by the board, retained)
${prefix}/availability  online | offline (retained, offline is the Last Will)`),
    el("p", { class: "muted" }, "With Home Assistant discovery on, the board shows up in Home Assistant as a device with an app and playlist picker, schedule and quiet-hours switches, a Message box, Next and Stop buttons and a sensor for what's showing."),
    el("h3", {}, "Trigger an automation"),
    pre(`curl -X POST ${base}/api/event/doorbell`),
    el("p", { class: "muted" }, "If you set a password in config.yaml, add ?token=PASSWORD to URLs or use curl -u :PASSWORD."),
  );
}

// ---- fonts ----
let previewFont = "normal";

async function updateFontPreview() {
  const f = fontCache.list.find(x => x.id === previewFont);
  const n = +$("fontPreviewSize").value || 1;
  const { rows } = await api("/api/render_text", {
    text: $("fontPreviewText").value || " ", font: previewFont,
    scale: f?.kind === "pixel" ? Math.min(n, 6) : 1, font_size: n,
  });
  const c = $("fontPreview");
  const W = 84, cell = 6;
  const grid = Array.from({ length: Math.max(rows.length, 7) }, (_, y) =>
    Array.from({ length: W }, (_, x) => (rows[y] || "")[x] === "1"));
  c.width = W * cell; c.height = grid.length * cell;
  renderDots(c.getContext("2d"), grid, cell, { seams: false });
}

function renderFontSettings() {
  const sel = fontSelect(previewFont);
  sel.onchange = safe(() => { previewFont = sel.value; $("fontPreviewSize").value = fontCache.list.find(f => f.id === previewFont)?.kind === "pixel" ? 2 : 14; return updateFontPreview(); });
  $("fontPreviewSel").replaceChildren(sel);
  const mine = fontCache.list.filter(f => f.kind === "uploaded");
  $("fontUserList").replaceChildren(...(mine.length ? mine.map(f => el("div", { class: "row tight" },
    el("span", {}, f.label),
    el("button", { class: "danger", onclick: safe(async () => {
      if (!confirm(`Delete font "${f.label}"?`)) return;
      await api("/api/fonts/delete", { id: f.id });
      await loadFonts();
    }) }, "Delete"))) : ["None"]));
  updateFontPreview().catch(() => {});
}
bus.addEventListener("fonts", renderFontSettings);
$("fontPreviewText").addEventListener("input", safe(updateFontPreview));
$("fontPreviewSize").addEventListener("input", safe(updateFontPreview));
$("fontUploadBtn").onclick = () => $("fontFile").click();
$("fontFile").onchange = safe(async e => {
  const file = e.target.files[0];
  if (!file) return;
  const j = await api(`/api/fonts?name=${encodeURIComponent(file.name)}`, file, { raw: true });
  previewFont = j.id;
  await loadFonts();
  toast(`Added font "${file.name}"`);
  e.target.value = "";
});

// ---- storage (storage.db) ----
const fmtBytes = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;

async function refreshStorage() {
  const s = await api("/api/storage");
  $("stPath").textContent = s.path;
  $("stSize").textContent = `${fmtBytes(s.size)} · ${s.files} file${s.files === 1 ? "" : "s"} (${fmtBytes(s.data_bytes)})`
    + (s.restore_pending ? " · a backup will be restored at the next restart" : "");
  $("stSync").textContent = s.last_sync ? new Date(s.last_sync * 1000).toLocaleString() : "Not yet";
}
$("stRefresh").onclick = safe(refreshStorage);
$("stBackup").onclick = () => el("a", { href: "/api/storage/backup", download: "" }).click();
$("stRestoreBtn").onclick = () => $("stRestoreFile").click();
$("stRestoreFile").onchange = safe(async e => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  if (!confirm(`Replace all saved data (settings, playlists, scenes, drawings, slideshows, fonts) with "${file.name}"?\n\n`
    + "It takes effect when the server restarts. Anything saved after the backup was made is lost.")) return;
  const j = await api("/api/storage/restore?confirm=1", file, { raw: true });
  toast(j.message);
  await refreshStorage();
});

// ---- gamepads (gamepad.py): status, live inputs, button mapping ----
const GP_KEYS = ["up", "down", "left", "right", "a", "b", "x", "y", "z", "l", "r", "start"];
let gpMapping = {}, gpDefaults = {}, gpTableDrawn = false;

function gpKeySelect(value) {
  return el("select", { "aria-label": "Game key" }, el("option", { value: "", selected: !value }, "Off"),
    ...GP_KEYS.map(k => el("option", { value: k, selected: k === value }, k === "start" ? "Start" : k.length === 1 ? k.toUpperCase() : k)));
}
function gpMapRow(code, value, editable) {
  const name = editable ? el("input", { type: "text", placeholder: "BTN_0x12c", "aria-label": "Code" }) : el("code", {}, code);
  return el("tr", { "data-code": code }, el("td", {}, name), el("td", {}, gpKeySelect(value)));
}
function drawGamepadMap() {
  const codes = [...new Set([...Object.keys(gpDefaults), ...Object.keys(gpMapping)])];
  $("gpMap").replaceChildren(...codes.map(c => gpMapRow(c, c in gpMapping ? gpMapping[c] : gpDefaults[c])));
  gpTableDrawn = true;
}
function gamepadMapFromTable() {
  const out = {};
  $("gpMap").querySelectorAll("tr").forEach(tr => {
    const input = tr.querySelector("input");
    const code = input ? input.value.trim() : tr.dataset.code;
    const key = tr.querySelector("select").value;
    if (code && (gpDefaults[code] ?? "") !== key) out[code] = key;
  });
  return out;
}
const timeAgo = t => { const s = Math.max(0, Math.round(Date.now() / 1000 - t)); return s < 60 ? `${s} s ago` : `${Math.round(s / 60)} min ago`; };
async function refreshGamepad() {
  const j = await api("/api/gamepad");
  gpDefaults = j.defaults || {};
  if (!gpTableDrawn) drawGamepadMap();
  $("gpStatus").textContent = j.message || (j.running ? "Running" : "Off");
  const pads = j.pads || [];
  $("gpPads").replaceChildren(...(pads.length ? pads.map(p => el("div", { class: "item" },
    el("span", { class: "name" }, p.connected ? `Player ${p.player}: ${p.name || "Gamepad"}` : p.path,
      el("small", {}, p.connected ? (p.held?.length ? `Holding ${p.held.join(", ")}` : p.last_input ? `Last input ${timeAgo(p.last_input)}` : "No input yet")
        : p.error || "Can't read"))))
    : [el("span", { class: "muted" }, j.running ? "None detected" : "Gamepads are off")]));
  const ev = (j.events || []).slice(-8).reverse();
  $("gpEvents").replaceChildren(...(ev.length ? ev.map(e => el("div", {},
    `Player ${e.player}: ${e.code} ${e.value ? (String(e.code).startsWith("ABS") ? e.value : "pressed") : "released"}${e.key ? ` → ${e.key}` : " (not mapped)"}`))
    : ["None yet"]));
}
// poll about once a second, only while the Controllers group is on screen
setInterval(() => {
  const group = document.querySelector('.setGroup[data-set="controllers"]');
  if (!document.hidden && !$("tab-settings").hidden && group && !group.hidden) refreshGamepad().catch(() => {});
}, 1000);
$("gpDeadzone").oninput = () => { $("gpDeadzoneVal").textContent = (+$("gpDeadzone").value).toFixed(2); };
$("gpSave").onclick = safe(() => save({ gamepad: { enabled: $("gpEnabled").checked, deadzone: +$("gpDeadzone").value } }));
$("gpAddCode").onclick = () => $("gpMap").append(gpMapRow("", "a", true));
$("gpMapSave").onclick = safe(() => save({ gamepad: { mapping: gamepadMapFromTable() } }));
$("gpMapReset").onclick = safe(async () => {
  if (!confirm("Use the default button mapping?")) return;
  await save({ gamepad: { mapping: {} } });
});

// ---- sub-navigation: one group of settings at a time, remembered per browser ----
function showSettingsGroup(name) {
  const groups = [...document.querySelectorAll(".setGroup")];
  if (!groups.some(g => g.dataset.set === name)) name = "general";
  groups.forEach(g => { g.hidden = g.dataset.set !== name; });
  document.querySelectorAll("#setNav [data-set]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.set === name)));
  try { localStorage.setItem("settingsGroup", name); } catch {}
  if (name === "controllers") refreshGamepad().catch(() => {});
}
$("setNav").addEventListener("click", e => { const b = e.target.closest("[data-set]"); if (b) showSettingsGroup(b.dataset.set); });
let savedGroup = "general";
try { savedGroup = localStorage.getItem("settingsGroup") || "general"; } catch {}
showSettingsGroup(savedGroup);
