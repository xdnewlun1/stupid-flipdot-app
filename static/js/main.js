import { $, api, board, bus, catalog, el, fixFor, icon, live, loadCatalog, loadDrawings, loadFonts, openSettings, refreshScenes, renderDots, safe, startPolling, toast, wsSend } from "./common.js";
import { setActive as setDrawActive } from "./draw.js";
import { STREAM_ID } from "./media.js";
import { fmtTime, loadVideos } from "./videos.js";
import { loadGalleries } from "./slideshows.js";
import { renderAppList } from "./apps.js";
import { loadPlaylists } from "./playlists.js";
import "./schedule.js";
import { loadAutomations } from "./automations.js";
import { loadCompositions, renderGallery } from "./layers.js";
import "./scenes.js";
import { loadSettings } from "./settings.js";
import { loadQuietHours } from "./boardcare.js";
import { loadGuestWall } from "./guestwall.js";
import { loadLeaderboard } from "./leaderboard.js";
import { loadFriends } from "./friends.js";
import "./people.js";
import "./plugins.js";
import "./mirror.js";
import { notifyResult } from "./notifications.js";

// ---- pages, grouped the way the sidebar and the phone tab bar show them ----
const PAGES = {
  now: ["now", "Now playing", "What the board shows right now and why. Stop it, skip ahead, or put something up quickly."],
  draw: ["create", "Draw", "Draw on the board, on a drawing layer that sits on top of what's running, or frame by frame as an animation."],
  import: ["create", "Import", "Photos and GIFs are saved to Pictures, videos to Videos, and several images or a .zip can become a slideshow. Each opens where it lands."],
  mirror: ["create", "Mirror", "Your silhouette in dots, from a phone, laptop or the board's own camera. It can also take over the board when someone walks up."],
  pictures: ["library", "Pictures", "Everything you've drawn, imported or saved: still pictures, animations and drawing layers."],
  apps: ["library", "Apps", "Clocks, text, widgets, patterns and games. Pick one to set it up and show it."],
  layers: ["library", "Layers", "Stack apps and pictures so they play at the same time, like a clock over Game of Life."],
  slideshows: ["library", "Slideshows", "A playlist made of your own images and GIFs: each one stays up for a set time, then the next one starts."],
  leaderboard: ["library", "Leaderboard", "Today's game, the best scores in every game, and the achievements earned on this board."],
  videos: ["library", "Videos", "Longer clips (WebM, MP4, MOV, GIF…), converted for the board. Pick one to play it, scrub through it or loop part of it."],
  playlists: ["programme", "Playlists", "Items that play one after another, each for a set time. The schedule decides when each playlist runs."],
  schedule: ["programme", "Schedule", "What plays when: by weekday, date or sunset, with one-off events, plus quiet hours for the night."],
  automations: ["programme", "Automations", "When something happens (music starts, an event arrives), change what's on the board."],
  guests: ["guests", "Guest wall", "Messages guests leave from their phones. Approve them before they scroll across the board."],
  friends: ["guests", "Friend messages", "Messages from people you gave a friend link: approve them, pin one, or show one again."],
  settings: ["settings", "Settings", "Location, fonts, connections and the board hardware."],
};
const GROUPS = {};
for (const [page, [group]] of Object.entries(PAGES)) (GROUPS[group] ||= []).push(page);
const GROUP_NAMES = { create: "Create", library: "Library", programme: "Programme" };
// hashes from before the reorganisation keep working
const OLD = { media: "import", scenes: "pictures", "": "now" };
const OLD_SETTINGS = { general: "location", board: "maintenance", storage: "backup", guests: null };

let currentPage = null;
const lastInGroup = {};
try { Object.assign(lastInGroup, JSON.parse(localStorage.getItem("lastPages") || "{}")); } catch {}

function showTab(spec) {
  let [name, sub] = String(spec ?? "").split("/");
  name = OLD[name] ?? name;
  if (name === "settings" && sub in OLD_SETTINGS) { if (OLD_SETTINGS[sub] === null) name = "guests"; sub = OLD_SETTINGS[sub] || undefined; }
  if (!PAGES[name]) name = "now";
  const [group, title, lede] = PAGES[name];
  const changed = name !== currentPage;
  currentPage = name;
  for (const p of Object.keys(PAGES)) $(`tab-${p}`).hidden = p !== name;
  document.querySelectorAll("#tabs [data-tab]").forEach(b => {
    if (b.dataset.tab === name) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  document.querySelectorAll("#tabbar [data-group]").forEach(b => {
    if (b.dataset.group === group) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  // the pages in this group, as a row of buttons (on phones, where there's no sidebar)
  const siblings = GROUPS[group];
  $("groupNav").hidden = siblings.length < 2;
  $("groupNav").replaceChildren(...(siblings.length < 2 ? [] : siblings.map(p =>
    el("button", { "data-tab": p, "aria-current": p === name ? "page" : false }, PAGES[p][1]))));
  $("groupNav").setAttribute("aria-label", `${GROUP_NAMES[group] || title} pages`);
  $("pageTitle").textContent = title;
  $("pageLede").textContent = lede;
  document.title = `${title} · Flipdot`;
  // the big board lives in Now playing and Draw; every other page has the compact strip
  const slot = name === "now" ? $("nowBoardSlot") : name === "draw" ? $("drawBoardSlot") : null;
  if (slot && $("device").parentElement !== slot) slot.append($("device"));
  $("device").hidden = !slot;
  $("nowStrip").hidden = !!slot;
  setDrawActive(name === "draw");
  document.body.dataset.tab = name;
  lastInGroup[group] = name;
  try { localStorage.setItem("lastPages", JSON.stringify(lastInGroup)); } catch {}
  const hash = "#" + name + (name === "settings" && sub ? "/" + sub : "");
  if (location.hash !== hash) history.replaceState(null, "", hash);
  if (name === "settings") {
    if (sub) bus.dispatchEvent(new CustomEvent("settingsGroup", { detail: sub }));
    loadSettings().catch(e => toast(e.message, true));
  }
  if (name === "schedule") api("/api/settings").then(loadQuietHours).catch(e => toast(e.message, true));
  if (name === "automations" && catalog.apps.length) loadAutomations().catch(e => toast(e.message, true));
  if (name === "guests") loadGuestWall().catch(e => toast(e.message, true));
  if (name === "leaderboard") loadLeaderboard().catch(e => toast(e.message, true));
  if (name === "friends") loadFriends().catch(e => toast(e.message, true));
  if (name === "now") renderWelcome().catch(() => {});
  if (changed) { window.scrollTo(0, 0); renderMini(); }
  bus.dispatchEvent(new CustomEvent("tabShown", { detail: name }));
}
document.addEventListener("click", e => {
  const b = e.target.closest("#tabs [data-tab], #groupNav [data-tab]");
  if (b) return showTab(b.dataset.tab);
  const g = e.target.closest("#tabbar [data-group]");
  if (g) showTab(lastInGroup[g.dataset.group] || GROUPS[g.dataset.group][0]);
});
window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
bus.addEventListener("tab", e => showTab(e.detail));
// a rename or delete changed references elsewhere on the server: reload those lists
bus.addEventListener("refs", () => {
  Promise.all([loadAutomations(), loadCompositions(), refreshScenes(), loadDrawings(), loadGalleries()]).catch(e => toast(e.message, true));
});

// ---- what's on the board, and why: a headline and a reason, shown in several places ----
function statusParts(r) {
  const what = r.description || "";
  const held = r.schedule_enabled && r.mode !== "schedule" ? " · the schedule is paused" : "";
  let s;
  if (r.live) {  // a browser is streaming video to the board (Import, Stream live)
    const mine = r.live.id === STREAM_ID;
    const what = `Live stream: ${r.live.name}`;
    const why = mine ? "From this browser. It stops if you close this tab" : "From another browser. It stops when that tab closes";
    return [what, r.live.target === "layer" ? `${why} · on a layer` : why];
  }
  if (r.video && !r.notification) {  // a video: name, position and whether it's playing
    const v = r.video;
    const state = r.paused ? "paused" : v.ended ? "finished" : "playing";
    const sp = v.speed && v.speed !== 1 ? ` at ${v.speed}×` : "";
    const where = r.mode === "app" ? "You started it" : r.mode === "playlist" ? `From the playlist "${r.playlist}"` : `From the schedule (${r.schedule_target || r.playlist})`;
    return [`${v.name} · ${fmtTime(v.pos)} / ${fmtTime(v.duration)} · ${state}${sp}`, where + held];
  }
  if (r.notification) {
    const n = r.notifications || {};
    s = [what, n.hold ? "Until it's dismissed, then back to what was on"
      : n.seconds ? `For ${n.seconds} s, then back to what was on` : "For a moment, then back to what was on"];
  } else if (r.mode === "manual") s = ["A still picture", "Nothing is running" + held];
  else if (r.mode === "app") s = [what, "You started it" + held];
  else if (r.mode === "playlist") s = [what || `Playlist "${r.playlist}" is empty`, `From the playlist "${r.playlist}"` + held];
  else if (!r.schedule_enabled) s = ["Nothing is running", "The schedule is turned off"];
  else if (r.playlist === "__off__") s = ["Board off (all black)", "From the schedule"];
  else if (!r.playlist && !r.app) s = ["Nothing scheduled right now", "The schedule is on"];
  else if (!r.playlist) s = [what || "Nothing", `From the schedule (${r.schedule_target}${r.schedule_rule != null ? `, rule ${r.schedule_rule + 1}` : ""})`];
  else s = [what || `Playlist "${r.playlist}" is empty`, `From the schedule (${r.playlist}${r.schedule_rule != null ? `, rule ${r.schedule_rule + 1}` : ""})`];
  if (r.paused) s[1] += " · paused";
  return s;
}

// playback buttons, built once per place that shows them; renderMode keeps them all in step
const ACTS = [
  ["resume", "schedule", "Resume schedule", () => api("/api/schedule/resume", {}).then(() => toast("Back on the schedule"))],
  ["prev", "prev", "Previous", () => api("/api/prev", {})],
  ["pause", "pause", "Pause", () => api("/api/pause", { paused: !board.runner?.paused })],
  ["next", "next", "Next", () => api("/api/next", {})],
  ["stop", "stop", "Stop", () => api("/api/stop", {})],
];
document.querySelectorAll("[data-controls]").forEach(box => box.append(...ACTS.map(([act, ic, label, fn]) =>
  el("button", { "data-act": act, class: act === "resume" ? "resume" : "", onclick: safe(fn) }, icon(ic), el("span", {}, label)))));

function setAct(act, { hidden, disabled, label, aria, iconName, pressed } = {}) {
  document.querySelectorAll(`[data-act="${act}"]`).forEach(b => {
    if (hidden !== undefined) b.hidden = hidden;
    if (disabled !== undefined) b.disabled = disabled;
    if (label) b.querySelector("span").textContent = label;
    if (aria) { b.setAttribute("aria-label", aria); b.title = aria; }
    if (iconName) b.querySelector("use").setAttribute("href", `#i-${iconName}`);
    if (pressed !== undefined) b.setAttribute("aria-pressed", String(pressed));
  });
}

let lastStatus = "";
function renderMode() {
  const r = board.runner;
  if (!r) return;
  const [what, why] = statusParts(r);
  if (what + why !== lastStatus) {  // the live region only changes when the words do
    lastStatus = what + why;
    document.querySelectorAll(".statusWhat").forEach(e => { e.textContent = what; });
    document.querySelectorAll(".statusWhy").forEach(e => { e.textContent = why; });
    $("statusLive").textContent = `${what}. ${why}`;
  }
  if (r.mode === "app" || r.mode === "playlist" || r.mode === "manual") markDone("shown");
  $("ctlLink").hidden = !r.interactive;
  $("quietState").hidden = !r.quiet_hours;
  renderConnection(live() ? "live" : "poll");
  const name = r.description || "what's running";
  const stoppable = r.mode !== "manual" || r.notification || !!r.live;
  setAct("stop", { disabled: !stoppable, aria: stoppable ? `Stop ${name}. The picture stays on the board.` : "Nothing is running" });
  const pic = r.skip === "picture", vid = r.skip === "video";
  setAct("next", { disabled: !r.can_next, label: pic ? "Next picture" : vid ? "Next video" : "Next",
    aria: !r.can_next ? "Nothing to skip. Next works for playlists, the schedule, slideshows and videos." : pic ? "Show the next picture in the slideshow" : vid ? "Play the next video in the library" : "Skip to the next playlist item" });
  setAct("prev", { hidden: !r.can_next, aria: pic ? "Show the previous picture in the slideshow" : vid ? "Play the previous video in the library" : "Go back to the previous playlist item" });
  const frozen = ["slideshow", "scene", "video"].includes(r.app);
  setAct("pause", { hidden: !r.can_pause && !r.paused, pressed: !!r.paused, iconName: r.paused ? "play" : "pause", label: r.paused ? "Resume" : "Pause",
    aria: r.paused ? "Carry on from here" : frozen ? "Pause on this picture" : "Stay on this item (the playlist stops moving on)" });
  setAct("resume", { hidden: !(r.schedule_enabled && r.mode !== "schedule"), aria: "Go back to what the schedule plays now" });
  renderWarning(r);
  renderLink(r);
}
// a running app that needs setup or can't load its data: say so, with the way to fix it
let lastWarn = "";
function renderWarning(r) {
  const who = r.problem_app || r.description || "This app";
  const msg = r.setup ? `${who} can't show anything yet: it needs ${r.setup}.`
    : r.error ? `${who} can't load its data: ${r.error}` : "";
  if (msg === lastWarn) return;
  lastWarn = msg;
  const box = $("statusWarn");
  box.hidden = !msg;
  if (!msg) return box.replaceChildren();
  const fix = fixFor(msg);
  box.replaceChildren(icon("warn"), el("span", {}, msg), fix ? el("button", { type: "button", onclick: fix.onclick }, fix.label) : "");
}
// buses the server can't reach: the preview shows what the board would display, not what the discs show
const hhmm = t => {
  const d = new Date(t * 1000);
  return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};
function linkText(link) {
  const down = link?.down || [];
  if (!down.length) return null;
  const byIp = {};
  for (const d of down) (byIp[d.ip] ||= []).push(d.port);
  const where = Object.entries(byIp).map(([ip, ports]) => `${ip}:${ports.sort((a, b) => a - b).join(", ")}`).join("; ");
  const since = hhmm(Math.min(...down.map(d => d.since || Date.now() / 1000)));
  const all = link.panels_down >= link.panels;
  return all
    ? { short: "Board offline",
        long: `Board not reachable (${where}, since ${since}). The preview shows what it would display; the discs still show the last picture they got.` }
    : { short: `${link.panels_down} panels offline`,
        long: `${link.panels_down} of ${link.panels} panels not reachable (${where}, since ${since}). Those panels keep what they showed before; the rest of the board is up to date.` };
}
const retryLink = safe(async () => {
  await api("/api/board/refresh", {});
  toast("Trying the board again");
});
let lastLink = "";
function renderLink(r) {
  const t = linkText(r.link);
  const key = t ? t.long : "";
  if (key === lastLink) return;
  lastLink = key;
  const box = $("linkWarn"), strip = document.querySelector(".stripLink");
  box.hidden = strip.hidden = !t;
  $("nowStrip").classList.toggle("linkDown", !!t);
  if (!t) { box.replaceChildren(); strip.replaceChildren(); return; }
  box.replaceChildren(icon("warn"), el("span", {}, t.long), el("button", { type: "button", onclick: retryLink }, "Retry now"));
  strip.replaceChildren(icon("warn"), el("span", {}, t.short));
  strip.title = t.long;
}
// the connection to this server, shown once (under the board preview)
let lastConn = "";
function renderConnection(state) {
  if (state === lastConn) return;
  lastConn = state;
  const text = { live: "Connected to the board server", poll: "Connected to the board server (checking every second)",
                 off: "Can't reach the board server. Retrying…" }[state];
  $("deviceState").replaceChildren(el("span", { class: `dot ${state}` }), text);
}
bus.addEventListener("state", renderMode);
bus.addEventListener("offline", () => renderConnection("off"));
bus.addEventListener("live", renderMode);
bus.addEventListener("reconnecting", renderMode);

// ---- the compact "now showing" strip: a small live preview, bigger on click ----
const mini = $("miniBoard"), mctx = mini.getContext("2d");
let miniQueued = false;
function renderMini() {
  if ($("nowStrip").hidden || miniQueued || !board.grid.length) return;
  miniQueued = true;
  requestAnimationFrame(() => {
    miniQueued = false;
    const cell = $("nowStrip").classList.contains("expanded") ? 6 : 3;
    if (mini.width !== board.W * cell) { mini.width = board.W * cell; mini.height = board.H * cell; }
    renderDots(mctx, board.grid, cell, { seams: false });
  });
}
bus.addEventListener("state", renderMini);
$("stripToggle").onclick = () => {
  const open = $("nowStrip").classList.toggle("expanded");
  $("stripToggle").setAttribute("aria-expanded", String(open));
  $("stripToggle").title = open ? "Show a smaller preview" : "Show a bigger preview";
  renderMini();
};

// ---- Now playing: quick things to show ----
const run = (spec, msg) => safe(async () => { await api("/api/run", spec); if (msg) toast(msg); });
$("qTextShow").onclick = safe(async () => {
  const text = $("qText").value.trim();
  if (!text) { $("qText").focus(); throw new Error("Type a message first."); }
  await api("/api/run", { app: "text", params: { text } });
  toast("Showing your message");
});
$("qTextFor").onclick = safe(async () => {
  const text = $("qText").value.trim();
  if (!text) { $("qText").focus(); throw new Error("Type a message first."); }
  const secs = Math.max(1, Math.round(+$("qSecs").value) || 10);
  const r = await api("/api/notify", { text, duration: secs, source: "this page" });
  toast(r.notify?.status === "show" ? `Showing your message for ${secs} s` : notifyResult(r));
});
$("qText").addEventListener("keydown", e => { if (e.key === "Enter") $("qTextShow").click(); });
$("qClock").onclick = run({ app: "clock" }, "Showing the clock");
$("qWeather").onclick = safe(async () => {
  // without a location the board would only show an error, so say it here, with the way to fix it
  if (!(await api("/api/settings")).weather?.name) throw new Error("The weather needs a weather location first (Settings, Location).");
  await api("/api/run", { app: "weather" });
  toast("Showing the weather");
});
$("qBlack").onclick = run({ app: "blank", params: { color: "black" } }, "The board is all black");
$("qMore").onclick = () => showTab("apps");

// ---- first run: a short getting-started list on Now playing, until it's hidden ----
const DONE_KEY = "gettingStarted";
let done = {};
try { done = JSON.parse(localStorage.getItem(DONE_KEY) || "{}"); } catch {}
function markDone(step) {
  if (done[step]) return;
  done[step] = true;
  try { localStorage.setItem(DONE_KEY, JSON.stringify(done)); } catch {}
  if (!$("welcome").hidden) renderWelcome().catch(() => {});
}
let welcomeOff = null;
async function renderWelcome() {
  const s = await api("/api/settings");
  welcomeOff = !!s.ui?.welcome_dismissed;
  const box = $("welcome");
  box.hidden = welcomeOff;
  if (welcomeOff) return;
  const located = !!s.weather?.name;
  const step = (ok, title, text, ...actions) => el("li", { class: ok ? "done" : "" },
    el("span", { class: "stepMark", "aria-hidden": "true" }, ok ? icon("check") : ""),
    el("div", {}, el("b", {}, title), ok ? el("span", { class: "sr" }, " (done)") : null, el("p", { class: "muted" }, text),
      actions.length ? el("div", { class: "row tight wrapRow" }, actions) : null));
  const phoneUrl = `${location.origin}/play`;
  box.replaceChildren(
    el("div", { class: "welcomeHead" }, el("h2", {}, "Getting started"),
      el("button", { class: "ghost", onclick: safe(async () => {
        await api("/api/settings", { ui: { welcome_dismissed: true } });
        box.hidden = true;
        toast("Hidden. The same things live in Settings and on the Apps page.");
      }) }, "Hide this")),
    el("ol", { class: "steps" },
      step(located, "Set your location", located ? `Weather uses ${s.weather.name}.` : "The weather app and weather automations need it.",
        located ? null : el("button", { onclick: () => openSettings("location", "wxSearch") }, "Set location")),
      step(!!done.shown, "Show something", "Put the clock or a message up. The schedule takes over again when you press Resume schedule.",
        done.shown ? null : el("button", { onclick: () => $("qClock").click() }, "Show the clock"),
        done.shown ? null : el("button", { onclick: () => $("qText").focus() }, "Type a message")),
      step(!!done.phone, "Use your phone as a controller", `Open ${phoneUrl} on your phone to play the board's games.`,
        el("a", { class: "button", href: "/play", target: "_blank", onclick: () => markDone("phone") }, icon("phone"), "Open the controller"))),
    el("p", { class: "muted" }, "Panels showing up in the wrong order? Step through them in ",
      el("a", { href: "#settings/hardware" }, "Settings → Board hardware"), "."));
}

// ---- keyboard control for games ----
const KEYS = {
  ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
  w: "up", s: "down", a: "left", d: "right", " ": "a", Enter: "b", x: "a", z: "b",
  q: "strafe_left", e: "strafe_right", r: "weapon", Shift: "run",  // Doom only; other games ignore them
};
function gameKey(e, action) {
  // Leave keys alone where focus needs them: fields get every key; buttons and links keep Enter and Space
  // (arrows still steer, so the game stays playable right after pressing Show on board).
  if (!board.runner?.interactive || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest?.("input, textarea, select, [contenteditable]")) return;
  if ((e.key === "Enter" || e.key === " ") && e.target.closest?.("button, a[href], summary, [role=button], [tabindex]:not(#board)")) return;
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
  document.querySelectorAll("[data-theme-choice]").forEach(b => {
    const on = b.dataset.themeChoice === (choice || "auto");
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", String(on));
  });
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
    await loadVideos();
    await refreshScenes();
    await loadCompositions();
    renderGallery();
    renderAppList();
    await loadPlaylists();
    await loadAutomations();
    loadGuestWall().catch(() => {});  // for the count of waiting messages
    loadFriends().catch(() => {});  // and of friend messages waiting for approval
  } catch (e) {
    toast(e.message, true);
  }
})();
