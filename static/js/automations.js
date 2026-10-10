// Automations (Playlists tab): "when <trigger>, do <action>" rules, saved on the server in rules.json.
import { $, api, catalog, compCache, el, iconButton, paramForm, safe, scenesCache, toast } from "./common.js";
import { playlistNames } from "./playlists.js";

let rules = [], info = {}, editing = null, draft = null;

const TRIGGERS = [
  ["music_start", "Music starts playing"],
  ["music_stop", "Music stops"],
  ["weather", "The weather turns"],
  ["event", "An event arrives"],
  ["idle", "The board is idle"],
];
const ACTIONS = [
  ["app", "Run an app"],
  ["composition", "Run a layer composition"],
  ["playlist", "Play a playlist"],
  ["notify", "Show a notification"],
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
  return Object.fromEntries(TRIGGERS)[t.type] || t.type;
}

function actionText(a) {
  if (a.type === "app") return `run ${catalog.byId[a.app]?.title || a.app}`;
  if (a.type === "composition") return `run composition "${a.composition}"`;
  if (a.type === "playlist") return `play "${a.playlist}"`;
  if (a.type === "notify") {
    const what = a.text ? `"${a.text}"` : a.scene ? `scene ${scenesCache.list.find(s => s.id === a.scene)?.name || a.scene}` : "image";
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

async function save(list = rules) {
  info = await api("/api/rules", { rules: list });
  rules = info.rules;
  render();
}

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
        iconButton("play", "Run the action now", safe(async () => { await api(`/api/rules/${r.id}/run`, {}); await loadAutomations(); })),
        iconButton("edit", editing === i ? "Close editor" : "Edit", () => openEditor(editing === i ? null : i)),
        iconButton("x", "Delete", safe(async () => {
          if (!confirm("Delete this automation?")) return;
          rules.splice(i, 1); editing = null; await save();
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
  s.onchange = () => onchange(s.value);
  return s;
};

function renderEditor() {
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
  } else {
    form.append(el("span"), el("small", { class: "muted" }, "Uses the Music Assistant server and player from Settings."));
  }

  row("Do", select(ACTIONS, a.type, v => { draft.action = { type: v, ...(v === "notify" ? { text: "", duration: 10 } : {}) }; rerender(); }));
  if (a.type === "app") {
    a.app ||= catalog.apps[0]?.id;
    row("App", select(catalog.apps.map(x => [x.id, `${x.category}: ${x.title}`]), a.app, v => { a.app = v; a.params = {}; rerender(); }));
  } else if (a.type === "composition") {
    a.composition ??= compCache.names[0] || "";
    row("Composition", select(compCache.names.map(n => [n, n]), a.composition, v => { a.composition = v; }));
  } else if (a.type === "playlist") {
    a.playlist ??= playlistNames()[0] || "";
    row("Playlist", select(playlistNames().map(n => [n, n]), a.playlist, v => { a.playlist = v; }));
  } else if (a.type === "notify") {
    const kind = a.scene != null ? "scene" : a.url != null ? "url" : "text";
    row("Show", select([["text", "Text"], ["scene", "Saved scene"], ["url", "Image from a URL"]], kind, v => {
      delete a.text; delete a.scene; delete a.url;
      a[v] = v === "scene" ? (scenesCache.list[0]?.id || "") : "";
      rerender();
    }));
    if (kind === "scene") row("Scene", select(scenesCache.list.map(s => [s.id, s.name]), a.scene, v => { a.scene = v; }));
    else {
      const input = el("input", { type: "text", value: a[kind] || "", placeholder: kind === "url" ? "https://…/picture.png" : "Message" });
      input.oninput = () => { a[kind] = input.value; };
      row(kind === "url" ? "Image URL" : "Text", input);
    }
    const dur = el("input", { type: "number", min: 1, value: a.duration ?? 10 });
    dur.oninput = () => { a.duration = +dur.value || 10; };
    row("For (seconds)", dur);
  }
  const cool = el("input", { type: "number", min: 0, value: draft.cooldown ?? 60 });
  cool.oninput = () => { draft.cooldown = Math.max(0, +cool.value || 0); };
  row("Wait between runs (s)", cool);

  if (a.type === "app" && catalog.byId[a.app]) appForm = paramForm(catalog.byId[a.app], a.params || {});
  box.replaceChildren(
    el("h3", {}, editing < rules.length ? "Edit automation" : "New automation"), form,
    appForm ? el("div", {}, el("h3", {}, `${catalog.byId[a.app].title} settings`), appForm.el) : "",
    el("div", { class: "row" },
      el("button", { class: "primary", onclick: safe(async () => {
        if (appForm) a.params = appForm.values();
        const next = rules.slice();
        next[editing] = draft;
        await save(next);  // on an error the editor stays open with the draft
        editing = null; draft = null; render();
        toast("Saved");
      }) }, "Save automation"),
      el("button", { onclick: () => openEditor(null) }, "Cancel")));
}

$("autoAdd").onclick = () => { editing = rules.length; draft = newRule(); render(); };
$("autoRefresh").onclick = safe(loadAutomations);
