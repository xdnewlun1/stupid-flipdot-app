// Automations page: "when <trigger>, do <action>" rules, saved on the server in rules.json.
import { $, api, appOptions, autosave, catalog, compCache, el, flushUndo, iconButton, paramForm, safe, scenesCache, toast, undoable } from "./common.js";
import { playlistNames } from "./playlists.js";

let rules = [], info = {}, editing = null, draft = null;

const TRIGGERS = [
  ["music_start", "Music starts playing"],
  ["music_stop", "Music stops"],
  ["weather", "The weather turns"],
  ["event", "An event arrives"],
  ["idle", "The board is idle"],
  ["presence", "Someone walks up (mirror camera)"],
  ["calendar", "A calendar event starts"],
];
const ACTIONS = [
  ["app", "Show an app"],
  ["composition", "Show a layer set"],
  ["playlist", "Play a playlist"],
  ["notify", "Show something for a few seconds"],
  ["schedule", "Resume the schedule"],
  ["black", "Turn all black"],
];
const WEATHER = [["rain", "rainy"], ["snow", "snowy"], ["clear", "clear"]];

export async function loadAutomations() {
  info = await api("/api/rules");
  rules = info.rules;
  render();
}

function triggerText(t) {
  if (t.type === "weather") return `Weather turns ${t.weather}`;
  if (t.type === "event") return `Event "${t.event}"`;
  if (t.type === "idle") return `Idle for ${t.minutes} min`;
  if (t.type === "presence") return t.state === "leave" ? "Everyone has left the camera" : "Someone walks up to the camera";
  if (t.type === "calendar") return t.keyword ? `A "${t.keyword}" event starts` : "A calendar event starts";
  return Object.fromEntries(TRIGGERS)[t.type] || t.type;
}

function actionText(a) {
  if (a.type === "app") return `show ${catalog.byId[a.app]?.title || a.app}`;
  if (a.type === "composition") return `show the layer set "${a.composition}"`;
  if (a.type === "playlist") return `play "${a.playlist}"`;
  if (a.type === "notify") {
    const what = a.text ? `"${a.text}"` : a.scene ? `picture ${scenesCache.list.find(s => s.id === a.scene)?.name || a.scene}` : "image";
    return `show ${what} for ${a.duration || 10} s`;
  }
  return a.type === "schedule" ? "resume the schedule" : "turn all black";
}

function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
}

// keepEditor: the editor is saving what you type, so it isn't redrawn under you
async function save(list = rules, { keepEditor = false } = {}) {
  info = await autosave("autoSaved", () => api("/api/rules", { rules: list }));
  rules = info.rules;
  keepEditorOpen = keepEditor;
  try { render(); } finally { keepEditorOpen = false; }
}
let keepEditorOpen = false;

function render() {
  const music = info.music == null ? "not checked (no music rule, or Music Assistant isn't set up)" : info.music ? "playing" : "not playing";
  $("autoStatus").textContent = `Music: ${music} · Weather: ${info.weather || "not checked yet"}`;
  const box = $("autoRules");
  if (!rules.length) box.replaceChildren(el("p", { class: "muted" }, "No automations yet. Press New automation to add one, for example to show a message when the doorbell event arrives."));
  else box.replaceChildren(el("div", { class: "items" }, rules.map((r, i) => {
    const on = el("input", { type: "checkbox", checked: r.enabled !== false, title: "Enabled", "aria-label": "Enabled" });
    on.onchange = safe(async () => { r.enabled = on.checked; await save(); });
    const fired = info.last_fired?.[r.id], err = info.errors?.[r.id];
    const detail = [r.cooldown ? `waits ${r.cooldown} s between runs` : "", fired ? `last ran ${ago(fired)}` : "", err ? `error: ${err}` : ""].filter(Boolean).join(" · ");
    return el("div", { class: "item" + (editing === i ? " editing" : "") }, on,
      el("div", { class: "name" }, r.name || `${triggerText(r.trigger)} → ${actionText(r.action)}`,
        el("small", {}, (r.name ? `${triggerText(r.trigger)} → ${actionText(r.action)}` : "") + (r.name && detail ? " · " : "") + detail)),
      el("div", { class: "group" },
        iconButton("play", "Try it now", safe(async () => { await api(`/api/rules/${r.id}/run`, {}); await loadAutomations(); })),
        iconButton("edit", editing === i ? "Close editor" : "Edit", () => openEditor(editing === i ? null : i)),
        iconButton("x", "Delete", safe(async () => {
          flushUndo();
          const [gone] = rules.splice(i, 1);
          editing = null; draft = null;
          await save();
          undoable(`Deleted ${r.name || "the automation"}`, { undo: async () => { rules.splice(i, 0, gone); await save(); } });
        }), "danger")));
  })));
  renderEditor();
}

function openEditor(i) {
  editing = i;
  draft = i === null ? null : JSON.parse(JSON.stringify(rules[i] || newRule()));
  render();
}

const newRule = () => ({ name: "", enabled: true, cooldown: 60, trigger: { type: "event", event: "doorbell" },
                         action: { type: "notify", text: "Someone is at the door", duration: 15 } });

const select = (opts, value, onchange) => {
  const s = el("select", {}, opts.map(([v, label]) => el("option", { value: v, selected: v === value }, label)));
  s.onchange = () => { pendingSave = true; onchange(s.value); };
  return s;
};

function renderEditor() {
  if (keepEditorOpen) return;
  const box = $("autoEditor");
  if (editing === null || !draft) return box.replaceChildren();
  const t = draft.trigger, a = draft.action;
  const form = el("div", { class: "form" });
  const row = (label, input) => form.append(el("label", {}, label), input);
  let appForm = null;
  const rerender = () => renderEditor();

  const name = el("input", { type: "text", value: draft.name || "", placeholder: "optional" });
  name.oninput = () => { draft.name = name.value; };
  row("Name", name);
  row("When", select(TRIGGERS, t.type, v => { draft.trigger = { type: v }; rerender(); }));
  if (t.type === "weather") {
    t.weather ||= "rain";
    row("Turns", select(WEATHER, t.weather, v => { t.weather = v; }));
  } else if (t.type === "event") {
    const ev = el("input", { type: "text", value: t.event || "", placeholder: "doorbell" });
    ev.oninput = () => { t.event = ev.value.trim(); };
    row("Event name", ev);
    form.append(el("span"), el("small", { class: "muted" },
      `Send it with POST ${location.origin}/api/event/NAME or the MQTT topic <prefix>/event (payload: the name).`));
  } else if (t.type === "idle") {
    t.minutes ??= 30;
    const m = el("input", { type: "number", min: 1, value: t.minutes });
    m.oninput = () => { t.minutes = +m.value || 1; };
    row("For (minutes)", m);
    form.append(el("span"), el("small", { class: "muted" }, "Idle means nothing is running and the picture hasn't changed."));
  } else if (t.type === "calendar") {
    t.until_end ??= true;
    const urls = el("textarea", { rows: 2, placeholder: "https://…/basic.ics" });
    urls.value = t.urls || "";
    urls.oninput = () => { t.urls = urls.value.trim(); };
    row("iCal links", urls);
    const kw = el("input", { type: "text", value: t.keyword || "", placeholder: "meeting, call (blank = any event)" });
    kw.oninput = () => { t.keyword = kw.value.trim(); };
    row("Title has", kw);
    const until = el("input", { type: "checkbox", checked: !!t.until_end });
    until.onchange = () => { t.until_end = until.checked; };
    row("Until it ends", until);
    form.append(el("span"), el("small", { class: "muted" },
      "With Until it ends, an app or layer set shows over what's on and goes away when the event ends. Try the On air app."));
  } else if (t.type === "presence") {
    t.state ||= "arrive";
    row("When someone", select([["arrive", "walks up"], ["leave", "has left"]], t.state, v => { t.state = v; }));
    form.append(el("span"), el("small", { class: "muted" }, "Uses the camera set up on the Mirror page, with its timings."));
  } else {
    form.append(el("span"), el("small", { class: "muted" }, "Uses the Music Assistant server and player from Settings."));
  }

  row("Do", select(ACTIONS, a.type, v => { draft.action = { type: v, ...(v === "notify" ? { text: "", duration: 10 } : {}) }; rerender(); }));
  if (a.type === "app") {
    a.app ||= "clock";
    const pick = el("select", {}, appOptions(a.app));
    pick.onchange = () => { pendingSave = true; a.app = pick.value; a.params = {}; rerender(); };
    row("App", pick);
  } else if (a.type === "composition") {
    a.composition ??= compCache.names[0] || "";
    row("Layer set", select(compCache.names.map(n => [n, n]), a.composition, v => { a.composition = v; }));
  } else if (a.type === "playlist") {
    a.playlist ??= playlistNames()[0] || "";
    row("Playlist", select(playlistNames().map(n => [n, n]), a.playlist, v => { a.playlist = v; }));
  } else if (a.type === "notify") {
    const kind = a.scene != null ? "scene" : a.url != null ? "url" : "text";
    row("Show", select([["text", "Text"], ["scene", "Saved picture"], ["url", "Image from a URL"]], kind, v => {
      delete a.text; delete a.scene; delete a.url;
      a[v] = v === "scene" ? (scenesCache.list[0]?.id || "") : "";
      rerender();
    }));
    if (kind === "scene") row("Picture", select(scenesCache.list.map(s => [s.id, s.name]), a.scene, v => { a.scene = v; }));
    else {
      const input = el("input", { type: "text", value: a[kind] || "", placeholder: kind === "url" ? "https://…/picture.png" : "Message" });
      input.oninput = () => { a[kind] = input.value; };
      row(kind === "url" ? "Image URL" : "Text", input);
    }
    const dur = el("input", { type: "number", min: 1, value: a.duration ?? 10 });
    dur.oninput = () => { a.duration = +dur.value || 10; };
    row("For (seconds)", dur);
    row("Priority", select([["low", "Low"], ["normal", "Normal"], ["high", "High"], ["critical", "Critical (shows even over a game)"]],
      a.priority || "normal", v => { a.priority = v; }));
    if (kind === "text") row("Icon", select([["", "None"], ...["bell", "mail", "door", "alert", "timer", "music", "weather", "calendar", "person"]
      .map(n => [n, n[0].toUpperCase() + n.slice(1)])], a.icon || "", v => { if (v) a.icon = v; else delete a.icon; }));
  }
  const cool = el("input", { type: "number", min: 0, value: draft.cooldown ?? 60 });
  cool.oninput = () => { draft.cooldown = Math.max(0, +cool.value || 0); };
  row("Wait between runs (s)", cool);

  if (a.type === "app" && catalog.byId[a.app]) appForm = paramForm(catalog.byId[a.app], a.params || {});
  const note = el("span", { class: "muted", role: "status" });
  const wrap = el("div", {},
    el("h3", {}, "Edit automation"), form,
    appForm ? el("div", {}, el("h3", {}, `${catalog.byId[a.app].title} settings`), appForm.el) : "",
    el("div", { class: "row" },
      el("button", { onclick: () => openEditor(null) }, "Done"),
      note));
  box.replaceChildren(wrap);
  // every change saves itself; one the server refuses (say, an event with no name) is explained here
  let timer = null;
  const index = editing;
  const changed = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (editing !== index || !draft) return;
      if (appForm) a.params = appForm.values();
      const next = rules.slice();
      next[index] = JSON.parse(JSON.stringify(draft));
      try {
        await save(next, { keepEditor: true });
        draft.id = rules[index]?.id;
        note.textContent = "";
      } catch (e) {
        note.textContent = `Not saved yet: ${e.message.replace(/^rule \d+: /, "")}`;
      }
    }, 400);
  };
  wrap.addEventListener("input", changed);
  wrap.addEventListener("change", changed);
  if (pendingSave) { pendingSave = false; changed(); }  // a select that redrew the editor
}
let pendingSave = false;

$("autoAdd").onclick = safe(async () => {
  flushUndo();
  await save([...rules, newRule()]);
  openEditor(rules.length - 1);
  $("autoEditor").scrollIntoView({ block: "nearest" });
});
$("autoRefresh").onclick = safe(loadAutomations);
