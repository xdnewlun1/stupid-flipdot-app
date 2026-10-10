// Settings tab: weather location, fonts, Music Assistant, MQTT, webhook docs.
import { $, api, bus, el, fontCache, fontSelect, loadFonts, renderDots, rowsToGrid, safe, toast } from "./common.js";

let settings = {};

export async function loadSettings() {
  settings = await api("/api/settings");
  const wx = settings.weather || {};
  $("wxCurrent").textContent = wx.name ? `${wx.name} (${(+wx.lat).toFixed(2)}, ${(+wx.lon).toFixed(2)})` : "not set";
  $("wxUnits").value = wx.units || "fahrenheit";
  $("trFps").value = settings.display?.transition_fps ?? 15;
  const sp = settings.sendspin || {};
  $("ssEnabled").checked = !!sp.enabled;
  $("ssName").value = sp.name || "Flipdot board";
  $("ssUnpaired").checked = !!sp.unpaired;
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
  $("mqStatus").textContent = !mq.enabled ? "Disabled" : mq.connected ? "Connected ✓" : `Not connected${mq.error ? ` — ${mq.error}` : ""}`;
  renderHooks(mq.prefix || "flipdot");
}

const save = async section => { settings = await api("/api/settings", section); await loadSettings(); toast("Saved"); };

$("wxFind").onclick = safe(async () => {
  const q = $("wxSearch").value.trim();
  if (!q) return;
  const { results } = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
  $("wxResults").replaceChildren(...(results.length ? results.map(r => el("div", {},
    el("a", { href: "#", onclick: safe(async e => {
      e.preventDefault();
      await save({ weather: { name: r.name, lat: r.lat, lon: r.lon, units: $("wxUnits").value } });
      $("wxResults").replaceChildren();
    }) }, r.name))) : ["No matches"]));
});
$("wxSearch").addEventListener("keydown", e => { if (e.key === "Enter") $("wxFind").click(); });
$("wxUnits").onchange = safe(() => save({ weather: { units: $("wxUnits").value } }));

let maPlayer = "";
async function testMusic() {
  $("maStatus").textContent = "connecting…";
  try {
    const { players } = await api("/api/music/players");
    const sel = $("maPlayer");
    sel.replaceChildren(el("option", { value: "" }, "Whatever is playing"),
      ...players.map(p => el("option", { value: p.name, selected: p.name === maPlayer }, `${p.name}${p.state === "playing" ? " ▶" : ""}`)));
    const now = players.find(p => p.state === "playing");
    $("maStatus").textContent = `Connected ✓ — ${players.length} player${players.length === 1 ? "" : "s"}` + (now ? `, playing "${now.title}" on ${now.name}` : "");
  } catch (e) {
    $("maStatus").textContent = "Not connected — " + e.message;
  }
}
async function refreshSendspin() {
  const j = await api("/api/sendspin");
  $("ssStatusLine").textContent = j.status + (j.receiving ? " · receiving visualizer data ✓" : "")
    + (j.metadata?.title ? ` · now: ${j.metadata.title}` : "") + (j.version ? ` · aiosendspin ${j.version}` : "")
    + (j.warning ? ` · ⚠ ${j.warning}` : "");
}
$("ssSave").onclick = safe(async () => {
  await save({ sendspin: { enabled: $("ssEnabled").checked, name: $("ssName").value.trim() || "Flipdot board",
                           unpaired: $("ssUnpaired").checked } });
  setTimeout(() => refreshSendspin().catch(() => {}), 1500);
});
$("ssPair").onclick = safe(async () => {
  await api("/api/sendspin/pair", {});
  toast("Pairing window open — finish pairing in Music Assistant (the board shows the code if one is needed)");
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
    el("h3", {}, "Show an image (URL or base64), optionally saving it as a scene"),
    pre(`curl -X POST ${base}/api/notify -H 'Content-Type: application/json' \\
  -d '{"url": "https://example.com/pic.png", "mode": "fit", "dither": true, "duration": 20}'

curl -X POST '${base}/api/image?mode=crop&dither=true&save=My%20photo' --data-binary @photo.jpg`),
    el("h3", {}, "Run an app / control playback"),
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
${prefix}/status    (published by the board, retained)`),
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
    }) }, "Delete"))) : ["none"]));
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
