// Settings page: location & display, fonts, AI drawing, Music Assistant, MQTT and webhook docs, gamepads, board hardware and maintenance (boardcare.js), backup.
import { $, api, autosave, bus, confirmDialog, el, fillField as fill, fontCache, fontSelect, forgetFields, loadFonts, renderDots, rowsToGrid, safe, toast, undoable, unsavedFields } from "./common.js";
import { loadBoardCare } from "./boardcare.js";

let settings = {};

export async function loadSettings() {
  settings = await api("/api/settings");
  const wx = settings.weather || {};
  $("wxCurrent").textContent = wx.name ? `${wx.name} (${(+wx.lat).toFixed(2)}, ${(+wx.lon).toFixed(2)})` : "Not set. Search for a city above.";
  fill("wxUnits", wx.units || "fahrenheit");
  fill("trFps", settings.display?.transition_fps ?? 15);
  const sp = settings.sendspin || {};
  fill("ssName", sp.name || "Flipdot board");
  fill("ssUnpaired", !!sp.unpaired, "checked");
  fill("ssServer", sp.server_url ?? "auto");
  fill("vaSource", settings.visualizer?.source || "off");
  const sc = settings.snapcast || {};
  fill("scHost", sc.host ?? "auto");
  fill("scPort", sc.port || 1704);
  fill("scCtrl", sc.control_port || 1705);
  fill("scName", sc.name || "Flipdot board");
  fill("scDelay", sc.delay_ms ?? 150);
  showAudioSource();
  const ma = settings.music_assistant || {};
  fill("maUrl", ma.url || "");
  fill("maToken", ma.token || "");
  maPlayer = ma.player || "";
  if (ma.url) testMusic().catch(() => {});
  const mq = settings.mqtt || {};
  fill("mqEnabled", !!mq.enabled, "checked");
  fill("mqHost", mq.host || "");
  fill("mqPort", mq.port || 1883);
  fill("mqUser", mq.username || "");
  fill("mqPass", mq.password || "");
  fill("mqPrefix", mq.prefix || "flipdot");
  fill("mqDiscovery", !!mq.discovery, "checked");
  fill("mqDiscPrefix", mq.discovery_prefix || "homeassistant");
  $("mqStatus").textContent = !mq.enabled ? "Off" : mq.connected ? "Connected" : `Not connected${mq.error ? `: ${mq.error}` : ""}`;
  renderHooks(mq.prefix || "flipdot");
  const ai = settings.ai || {};
  fill("aiKey", ai.api_key || "");
  aiModels().then(models => {
    const keep = unsavedFields().includes("aiModel") ? $("aiModel").value : null;
    $("aiModel").replaceChildren(...models.map(([v, label]) => el("option", { value: v }, label)));
    if (keep) $("aiModel").value = keep;
    fill("aiModel", ai.model || models[0][0]);
  }).catch(() => {});
  const gp = settings.gamepad || {};
  fill("gpEnabled", gp.enabled !== false, "checked");
  fill("gpDeadzone", gp.deadzone ?? 0.35);
  fill("gpMenu", gp.menu_button || "hold_start");
  $("gpDeadzoneVal").textContent = (+$("gpDeadzone").value).toFixed(2);
  gpMapping = { ...(gp.mapping || {}) };
  refreshGamepad().catch(() => {});
  refreshStorage().catch(() => {});
  await loadBoardCare(settings);
}

// fields per settings section, so saving one section refreshes only those (drafts elsewhere stay)
const FIELDS = {
  weather: ["wxUnits"], display: ["trFps"], sendspin: ["ssName", "ssUnpaired", "ssServer"],
  visualizer: ["vaSource"], snapcast: ["scHost", "scPort", "scCtrl", "scName", "scDelay"],
  music_assistant: ["maUrl", "maToken"], ai: ["aiKey", "aiModel"],
  mqtt: ["mqEnabled", "mqHost", "mqPort", "mqUser", "mqPass", "mqPrefix", "mqDiscovery", "mqDiscPrefix"],
  gamepad: ["gpEnabled", "gpDeadzone", "gpMenu"], quiet_hours: ["qhEnabled", "qhStart", "qhEnd", "qhMax", "qhSlow"],
  board_health: ["wearAuto", "wearTime"],
};
export const savedSection = section => forgetFields(Object.keys(section).flatMap(k => FIELDS[k] || []));
const save = async section => {
  settings = await api("/api/settings", section);
  savedSection(section);
  await loadSettings();
};

// Every setting saves itself when you change it (text fields when you leave them), with "Saved" next to its heading.
const SECTIONS = [
  { note: "wxSaved", ids: ["wxUnits"], build: () => ({ weather: { units: $("wxUnits").value } }) },
  { note: "dispSaved", ids: ["trFps"], build: () => ({ display: { transition_fps: Math.max(5, Math.min(30, +$("trFps").value || 15)) } }) },
  { note: "aiSaved", ids: ["aiKey", "aiModel"], build: () => ({ ai: { api_key: $("aiKey").value.trim(), model: $("aiModel").value } }) },
  { note: "maSaved", ids: ["maUrl", "maToken", "maPlayer"], after: () => testMusic(),
    build: () => ({ music_assistant: { url: $("maUrl").value.trim(), token: $("maToken").value.trim(), player: (maPlayer = $("maPlayer").value) } }) },
  { note: "vaSaved", ids: ["vaSource"], after: () => setTimeout(showAudioSource, 1500),
    build: () => ({ visualizer: { source: $("vaSource").value } }) },
  { note: "scSaved", ids: ["scHost", "scPort", "scCtrl", "scName", "scDelay"],
    after: () => setTimeout(() => refreshSnapcast().catch(() => {}), 2500),
    build: () => ({ snapcast: { host: $("scHost").value.trim() || "auto", port: +$("scPort").value || 1704,
                                control_port: +$("scCtrl").value || 1705, name: $("scName").value.trim() || "Flipdot board",
                                delay_ms: Math.max(50, Math.min(2000, +$("scDelay").value || 150)) } }) },
  { note: "spSaved", ids: ["ssName", "ssUnpaired", "ssServer"],
    after: () => setTimeout(() => refreshSendspin().catch(() => {}), 1500),
    build: () => ({ sendspin: { name: $("ssName").value.trim() || "Flipdot board",
                                unpaired: $("ssUnpaired").checked, server_url: $("ssServer").value.trim() } }) },
  { note: "mqSaved", ids: ["mqEnabled", "mqHost", "mqPort", "mqUser", "mqPass", "mqPrefix", "mqDiscovery", "mqDiscPrefix"],
    after: () => setTimeout(() => loadSettings().catch(() => {}), 2500),  // show the connection result
    build: () => ({ mqtt: {
      enabled: $("mqEnabled").checked, host: $("mqHost").value.trim(), port: +$("mqPort").value || 1883,
      username: $("mqUser").value, password: $("mqPass").value, prefix: $("mqPrefix").value.trim() || "flipdot",
      discovery: $("mqDiscovery").checked, discovery_prefix: $("mqDiscPrefix").value.trim() || "homeassistant",
    } }) },
  { note: "gpSaved", ids: ["gpEnabled", "gpDeadzone", "gpMenu"],
    build: () => ({ gamepad: { enabled: $("gpEnabled").checked, deadzone: +$("gpDeadzone").value, menu_button: $("gpMenu").value } }) },
];
for (const sec of SECTIONS) {
  let timer = null;
  const go = () => {
    clearTimeout(timer);
    timer = setTimeout(safe(async () => { await autosave(sec.note, () => save(sec.build())); sec.after?.(); }), 300);
  };
  sec.ids.forEach(id => $(id).addEventListener("change", go));
}
// typed-but-unsaved settings survive tab switches; warn before the page itself goes away
window.addEventListener("beforeunload", e => { if (unsavedFields().length) e.preventDefault(); });

$("wxFind").onclick = safe(async () => {
  const q = $("wxSearch").value.trim();
  if (!q) return;
  const { results } = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
  $("wxResults").replaceChildren(...(results.length ? results.map(r =>
    el("button", { class: "ghost", onclick: safe(async () => {
      await autosave("wxSaved", () => save({ weather: { name: r.name, lat: r.lat, lon: r.lon, units: $("wxUnits").value } }));
      $("wxResults").replaceChildren();
    }) }, r.name)) : [el("span", { class: "muted" }, `No places match "${q}". Try a nearby larger city.`)]));
});
$("wxSearch").addEventListener("keydown", e => { if (e.key === "Enter") $("wxFind").click(); });

let aiModelList = null;
const aiModels = async () => (aiModelList ||= (await api("/api/ai")).models);

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
// only the chosen source's fields show; the status of a running link refreshes while it's on screen
function showAudioSource() {
  const source = $("vaSource").value;
  $("ssBlock").hidden = source !== "sendspin";
  $("scBlock").hidden = source !== "snapcast";
  if (source === "sendspin") refreshSendspin().catch(() => {});
  if (source === "snapcast") refreshSnapcast().catch(() => {});
}
async function refreshSnapcast() {
  const j = await api("/api/snapcast");
  const parts = [j.status];
  if (j.codec) parts.push(`${j.codec} ${j.sample_format || ""}`.trim());
  if (j.buffer_ms != null) parts.push(`buffer ${j.buffer_ms} ms, latency ${j.latency_ms} ms`);
  if (j.version) parts.push(`snapclient ${j.version}`);
  if (j.muted) parts.push("muted in Music Assistant, so no audio is sent");
  if (j.name_note) parts.push(j.name_note);
  $("scStatus").textContent = parts.join(" · ");
}
setInterval(() => {
  if (document.body.dataset.tab === "settings" && !$("scBlock").hidden && !$("scBlock").closest(".setGroup").hidden)
    refreshSnapcast().catch(() => {});
}, 3000);
async function refreshSendspin() {
  const j = await api("/api/sendspin");
  $("ssStatusLine").textContent = j.status + (j.receiving ? " · receiving visualizer data" : "")
    + (j.metadata?.title ? ` · now: ${j.metadata.title}` : "") + (j.version ? ` · aiosendspin ${j.version}` : "")
    + (j.warning ? ` · Warning: ${j.warning}` : "");
}
$("ssFind").onclick = safe(async () => {
  $("ssStatusLine").textContent = "Searching the network…";
  const { servers } = await api("/api/sendspin/discover");
  if (!servers.length) { $("ssStatusLine").textContent = "No Music Assistant Sendspin server found on the network. Enter its address by hand."; return; }
  $("ssServer").value = servers[0].url;
  $("ssServer").dispatchEvent(new Event("change"));  // saves it
  $("ssStatusLine").textContent = `Found ${servers.map(x => x.url).join(", ")}. Saved; restart the server to connect.`;
});
$("ssPair").onclick = safe(async () => {
  await api("/api/sendspin/pair", {});
  toast("Pairing window open. Finish pairing in Music Assistant; the board shows a code if one is needed.");
});
$("maTest").onclick = safe(testMusic);

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
      if (!await confirmDialog({ title: `Delete the font "${f.label}"?`, message: "Apps and layers that use it switch to the standard pixel font.", confirmText: "Delete", danger: true })) return;
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
  if (!await confirmDialog({ title: `Restore "${file.name}"?`, confirmText: "Restore", danger: true,
    message: "It replaces all saved data (settings, playlists, scenes, drawings, slideshows, fonts) when the server restarts. Anything saved after the backup was made is lost." })) return;
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
$("gpAddCode").onclick = () => $("gpMap").append(gpMapRow("", "a", true));
// the mapping table saves itself too
let gpMapTimer = null;
const saveMapping = mapping => autosave("gpMapSaved", () => save({ gamepad: { mapping } }));
$("gpMap").addEventListener("change", () => {
  clearTimeout(gpMapTimer);
  gpMapTimer = setTimeout(safe(async () => { await saveMapping(gamepadMapFromTable()); gpMapping = gamepadMapFromTable(); }), 300);
});
$("gpMapReset").onclick = safe(async () => {
  const before = { ...gpMapping };
  await saveMapping({});
  gpMapping = {};
  drawGamepadMap();
  undoable("Back to the default button mapping", { undo: async () => { await saveMapping(before); gpMapping = before; drawGamepadMap(); } });
});

// ---- sub-navigation: one group of settings at a time, remembered per browser ----
const OLD_GROUPS = { general: "location", board: "maintenance", storage: "backup" };
function showSettingsGroup(name) {
  const groups = [...document.querySelectorAll(".setGroup")];
  name = OLD_GROUPS[name] || name;
  if (!groups.some(g => g.dataset.set === name)) name = "location";
  groups.forEach(g => { g.hidden = g.dataset.set !== name; });
  document.querySelectorAll("#setNav [data-set]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.set === name)));
  try { localStorage.setItem("settingsGroup", name); } catch {}
  if (name === "controllers") refreshGamepad().catch(() => {});
  if (document.body.dataset.tab === "settings") history.replaceState(null, "", `#settings/${name}`);
}
$("setNav").addEventListener("click", e => { const b = e.target.closest("[data-set]"); if (b) showSettingsGroup(b.dataset.set); });
bus.addEventListener("settingsGroup", e => showSettingsGroup(e.detail));
let savedGroup = "location";
try { savedGroup = localStorage.getItem("settingsGroup") || "location"; } catch {}
showSettingsGroup(savedGroup);
