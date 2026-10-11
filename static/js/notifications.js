// Notifications: the queue on Now playing (on the board, waiting, held for do-not-disturb, recent with Show again,
// a test button) and Settings → Notifications (defaults, do-not-disturb, games, ntfy topics).
import { $, api, autosave, board, bus, el, iconButton, safe, toast } from "./common.js";

const PRIORITY = { low: "Low", normal: "Normal", high: "High", critical: "Critical" };
const SOUND = { none: "None", tick: "Tick", clatter: "Clatter", sweep: "Sweep" };
const STATE = { showing: "On the board", banner: "Banner over the game", waiting: "Waiting", held: "Held",
                shown: "Shown", dropped: "Dropped (do not disturb)" };

// what the queue did with a notification, in words (for toasts)
export function notifyResult(r) {
  const s = r?.notify?.status || r?.status;
  return {
    show: "Showing it now", banner: "Showing it as a banner over the game", replace: "Updated the one with the same key",
    queued: "Waiting for its turn", held: "Held: do not disturb is on", dropped: "Dropped: do not disturb is on",
    duplicate: "Already shown a moment ago", busy: "Not shown: the board is busy",
  }[s] || "Sent";
}

// ---- Now playing ----------------------------------------------------------------------------------------------

const panel = $("notifPanel");
let last = "";
let data = null;

function when(t) {
  const d = new Date(t * 1000);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function row(n, ...actions) {
  const meta = [PRIORITY[n.priority], STATE[n.state] && n.state !== "shown" ? STATE[n.state] : null,
                n.source, n.hold ? "stays until dismissed" : null, n.shown_at && n.state !== "waiting" && n.state !== "held" ? when(n.shown_at) : null]
    .filter(Boolean).join(" · ");
  return el("div", { class: `item notifItem prio-${n.priority}` },
    el("span", { class: "name" }, n.text, el("small", {}, meta)), ...actions);
}

async function act(body, msg) {
  data = await api("/api/notifications", body);
  render();
  if (msg) toast(msg);
}

function render() {
  if (!panel || !data) return;
  const d = data;
  const dismiss = n => iconButton("x", `Dismiss "${n.text}"`, safe(() => act({ action: "dismiss", id: n.id })));
  const on = [d.current, d.banner].filter(Boolean);
  const waiting = [...d.pending, ...d.held];
  const testPrio = el("select", { "aria-label": "Priority of the test notification" },
    ...Object.entries(PRIORITY).map(([v, l]) => el("option", { value: v, selected: v === "normal" }, l)));
  panel.replaceChildren(...[
    el("div", { class: "bar notifHead" },
      el("h3", {}, "Notifications"),
      d.dnd ? el("span", { class: "notifDnd" }, "Do not disturb") : null,
      el("span", { class: "spacer" }),
      el("div", { class: "row tight" }, testPrio,
        el("button", { onclick: safe(async () => {
          const r = await api("/api/notifications", { action: "test", priority: testPrio.value });
          data = r; render(); toast("Sent a test notification");
        }) }, "Send a test"))),
    on.length ? el("div", { class: "items" }, ...on.map(n => row(n, dismiss(n)))) : null,
    waiting.length ? el("div", { class: "bar notifSub" },
      el("h4", {}, `Waiting (${waiting.length})`), el("span", { class: "spacer" }),
      el("button", { onclick: safe(() => act({ action: "clear" }, "Cleared the notifications")) }, "Clear all")) : null,
    waiting.length ? el("div", { class: "items" }, ...waiting.map(n => row(n, dismiss(n)))) : null,
    !on.length && !waiting.length ? el("p", { class: "muted" }, d.busy === "game"
      ? "A game is on: low and normal notifications wait until it ends."
      : "Nothing waiting. Messages from the API, Home Assistant, ntfy, timers and automations line up here.") : null,
    d.history.length ? el("details", { class: "notifHistory" },
      el("summary", {}, `Recent (${d.history.length})`),
      el("div", { class: "items" }, ...d.history.map(n => row(n,
        n.can_reshow ? el("button", { onclick: safe(() => act({ action: "reshow", id: n.id }, "Showing it again")) }, "Show again") : null)))) : null,
  ].filter(Boolean));
}

async function refresh() {
  data = await api("/api/notifications");
  render();
}

if (panel) {
  bus.addEventListener("state", () => {
    const key = JSON.stringify(board.runner?.notifications || null);
    if (key !== last) { last = key; refresh().catch(() => {}); }
  });
  refresh().catch(() => {});
}

// ---- Settings → Notifications ---------------------------------------------------------------------------------

const box = $("notifSettings");
let cfg = null;

function field(label, control, hint) {
  const id = control.id;
  return [el("label", { for: id }, label), hint ? el("div", { class: "row tight wrapRow" }, control, el("span", { class: "muted" }, hint)) : control];
}

function select(id, options, value) {
  return el("select", { id }, ...Object.entries(options).map(([v, l]) => el("option", { value: v, selected: v === value }, l)));
}

function renderSettings(s, ntfyStatus) {
  const c = s.notifications || {};
  const dnd = { mode: "quiet", start: "22:00", end: "07:00", during: "hold", now: false, ...(c.dnd || {}) };
  const nt = { enabled: false, server: "https://ntfy.sh", topics: [], token: "", ...(c.ntfy || {}) };
  const num = (id, v, min, max) => el("input", { id, type: "number", min, max, value: v, inputmode: "numeric", style: "width:80px" });
  const custom = dnd.mode === "custom";
  const ntState = !nt.enabled ? "Off" : ntfyStatus?.connected ? `Connected, ${ntfyStatus.received} received`
    : `Not connected${ntfyStatus?.error ? `: ${ntfyStatus.error}` : ""}`;
  box.replaceChildren(
    el("h2", {}, "Notifications ", el("span", { class: "savedNote", id: "ntSaved", role: "status" })),
    el("p", { class: "lede" }, "A notification waits for the one on the board unless it has a higher priority. Critical ones always show at once, even over a game. High ones show over a game as a banner along the bottom. Low and normal ones wait for the game to end, unless you turn on the banner below."),
    el("div", { class: "form" },
      ...field("Show for", num("ntDur", c.duration ?? 10, 1, 3600), "seconds, unless the notification says otherwise"),
      ...field("Sound pattern", select("ntSound", SOUND, c.sound || "none"), "discs flipped for the click before a notification shows"),
      ...field("Skip repeats within", num("ntDedupe", c.dedupe_s ?? 60, 0, 3600), "seconds (0 shows every copy)"),
    ),
    el("h3", { class: "spaced" }, "While a game is played"),
    el("div", { class: "form" },
      el("label", { for: "ntBanner" }, "Banner over games"),
      el("label", {}, el("input", { id: "ntBanner", type: "checkbox", checked: !!c.banner_over_games }),
        " Show low and normal notifications as a strip over the game instead of waiting"),
      ...field("Banner position", select("ntBannerPos", { bottom: "Bottom", top: "Top" }, c.banner_position || "bottom")),
    ),
    el("h3", { class: "spaced" }, "Do not disturb"),
    el("p", { class: "muted" }, "Low and normal notifications are held, then shown as a summary when it ends. High ones show without the sound pattern. Critical ones always show."),
    el("div", { class: "form" },
      ...field("When", select("ntDndMode", { quiet: "During quiet hours", custom: "Its own hours", off: "Never" }, dnd.mode)),
      ...(custom ? [
        el("label", { for: "ntDndStart" }, "From"),
        el("div", { class: "row tight" }, el("input", { id: "ntDndStart", type: "time", value: dnd.start }),
          el("span", { class: "muted" }, "to"), el("input", { id: "ntDndEnd", type: "time", value: dnd.end, "aria-label": "Until" })),
      ] : []),
      ...field("Held notifications", select("ntDndDuring", { hold: "Show a summary when it ends", drop: "Drop them" }, dnd.during)),
      el("label", { for: "ntDndNow" }, "Right now"),
      el("label", {}, el("input", { id: "ntDndNow", type: "checkbox", checked: !!dnd.now }), " Do not disturb until I turn it off"),
    ),
    el("h3", { class: "spaced" }, "ntfy"),
    el("p", { class: "muted" }, "Subscribe to ntfy topics so any phone, script or cron job can send a notification: ",
      el("code", {}, "curl -d \"Washer done\" ntfy.sh/your-topic"), ". ntfy priority 1-5 maps to low, low, normal, high, critical; tags such as door, warning or calendar pick the icon. Anyone who knows a topic on ntfy.sh can post to it, so pick a long, unguessable name or use your own server."),
    el("div", { class: "form" },
      el("label", { for: "ntOn" }, "Subscribe"),
      el("label", {}, el("input", { id: "ntOn", type: "checkbox", checked: !!nt.enabled }), " ", el("span", { id: "ntState", class: "muted" }, ntState)),
      ...field("Server", el("input", { id: "ntServer", type: "text", inputmode: "url", value: nt.server || "https://ntfy.sh", class: "grow", placeholder: "https://ntfy.sh" })),
      ...field("Topics", el("input", { id: "ntTopics", type: "text", value: (nt.topics || []).join(", "), class: "grow", placeholder: "my-board-7f3k, doorbell-7f3k" })),
      ...field("Access token", el("input", { id: "ntToken", type: "password", value: nt.token || "", autocomplete: "off", placeholder: "Only for protected topics" })),
    ),
    el("h3", { class: "spaced" }, "From scripts"),
    el("pre", {}, `curl -X POST ${location.origin}/api/notify -H 'Content-Type: application/json' \\
  -d '{"text": "Washer done", "priority": "high", "icon": "bell",
       "sound": "tick", "duration": 15, "repeat": 2, "hold": false, "key": "washer"}'`),
    el("p", { class: "muted" }, "Icons: bell, mail, door, alert, timer, music, weather, calendar, person. Sound patterns: none, tick, clatter, sweep. A key replaces the earlier notification with the same key instead of adding another; hold keeps it on until it's dismissed (here, or any button on a controller)."),
  );
  for (const e of box.querySelectorAll("input, select"))
    e.addEventListener("change", () => { clearTimeout(timer); timer = setTimeout(save, e.type === "checkbox" || e.tagName === "SELECT" ? 0 : 300); });
}

let timer = null;
const save = safe(() => autosave("ntSaved", async () => {
  const v = id => $(id)?.value;
  const old = cfg?.notifications || {};
  const body = {
    duration: Math.max(1, Math.min(3600, +v("ntDur") || 10)),
    sound: v("ntSound"),
    dedupe_s: Math.max(0, Math.min(3600, +v("ntDedupe") || 0)),
    banner_over_games: $("ntBanner").checked,
    banner_position: v("ntBannerPos"),
    dnd: { ...(old.dnd || {}), mode: v("ntDndMode"), during: v("ntDndDuring"), now: $("ntDndNow").checked,
           ...($("ntDndStart") ? { start: v("ntDndStart") || "22:00", end: v("ntDndEnd") || "07:00" } : {}) },
    ntfy: { enabled: $("ntOn").checked, server: (v("ntServer") || "https://ntfy.sh").trim(),
            topics: v("ntTopics").split(/[\s,]+/).filter(Boolean), token: v("ntToken") },
  };
  const modeChanged = body.dnd.mode !== (old.dnd?.mode || "quiet");
  cfg = await api("/api/settings", { notifications: body });
  if (modeChanged) await loadNotificationSettings(cfg);
  setTimeout(() => api("/api/notifications").then(r => { const s = $("ntState"); if (s && $("ntOn").checked) s.textContent = r.ntfy.connected ? `Connected, ${r.ntfy.received} received` : `Not connected${r.ntfy.error ? `: ${r.ntfy.error}` : ""}`; }).catch(() => {}), 1500);
}));

export async function loadNotificationSettings(s) {
  if (!box) return;
  cfg = s || await api("/api/settings");
  const st = await api("/api/notifications").catch(() => ({}));
  renderSettings(cfg, st.ntfy);
}

if (box) {
  $("setNav").addEventListener("click", e => { if (e.target.closest('[data-set="notifications"]')) loadNotificationSettings().catch(() => {}); });
  bus.addEventListener("tab", e => {
    if (e.detail === "settings" && !box.closest(".setGroup").hidden) loadNotificationSettings().catch(() => {});
  });
  loadNotificationSettings().catch(() => {});
}
