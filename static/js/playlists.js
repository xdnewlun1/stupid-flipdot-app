// Playlists & schedule tab.
import { $, api, bus, catalog, describeItem, el, iconButton, paramForm, safe, toast } from "./common.js";

let playlists = {}, current = null, editing = null;
let schedule = { enabled: false, default: "", rules: [] }, OFF = "__off__";
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const playlistNames = () => Object.keys(playlists);

export async function loadPlaylists() {
  playlists = (await api("/api/playlists")).playlists;
  const s = await api("/api/schedule");
  schedule = s.schedule; OFF = s.off;
  if (!current || !playlists[current]) current = Object.keys(playlists)[0] || null;
  render();
}

async function savePlaylists() {
  playlists = (await api("/api/playlists", { playlists })).playlists;
  bus.dispatchEvent(new Event("playlists"));
  render();
}

async function saveSchedule() {
  schedule = (await api("/api/schedule", { schedule })).schedule;
  renderSchedule();
}

export async function addToPlaylist(name, item) {
  playlists[name].items.push(item);
  await savePlaylists();
}

function render() {
  const sel = $("plSelect");
  sel.replaceChildren(...Object.keys(playlists).map(n => el("option", { value: n, selected: n === current }, n)));
  $("plTransition").replaceChildren(...catalog.transitions.map(t =>
    el("option", { value: t, selected: playlists[current]?.transition === t }, t)));
  $("plTransDur").value = playlists[current]?.transition_duration ?? 1;
  $("plAddApp").replaceChildren(...catalog.apps.map(a => el("option", { value: a.id }, `${a.category}: ${a.title}`)));
  const pl = playlists[current];
  const box = $("plItems");
  if (!pl) { box.replaceChildren(el("p", { class: "muted" }, "No playlists yet — press New.")); renderSchedule(); return; }
  if (!pl.items.length) box.replaceChildren(el("p", { class: "muted" }, "Empty playlist. Add items below or from the Apps tab."));
  else box.replaceChildren(el("div", { class: "items" }, pl.items.map((item, i) => {
    const { title, detail } = describeItem(item);
    const dur = el("input", { type: "number", min: 1, value: item.duration, title: "seconds" });
    const trans = el("select", { title: "Transition into this item" },
      el("option", { value: "" }, "playlist transition"),
      catalog.transitions.map(t => el("option", { value: t, selected: item.transition === t }, t)));
    trans.onchange = safe(async () => { if (trans.value) item.transition = trans.value; else delete item.transition; await savePlaylists(); });
    dur.onchange = safe(async () => { item.duration = Math.max(1, +dur.value || 30); await savePlaylists(); });
    const move = d => safe(async () => {
      const j = i + d;
      if (j < 0 || j >= pl.items.length) return;
      [pl.items[i], pl.items[j]] = [pl.items[j], pl.items[i]];
      await savePlaylists();
    });
    return el("div", { class: "item" + (editing === i ? " editing" : "") },
      el("div", { class: "name" }, title, el("small", {}, detail)),
      el("label", { class: "muted" }, dur, " s"), trans,
      el("div", { class: "group" },
        iconButton("up", "Move up", move(-1)),
        iconButton("down", "Move down", move(1)),
        iconButton("edit", editing === i ? "Close editor" : "Edit settings", () => { editing = editing === i ? null : i; render(); }),
        iconButton("play", "Run this item now", safe(() => api("/api/run", { app: item.app, params: item.params }))),
        iconButton("x", "Remove from playlist", safe(async () => { pl.items.splice(i, 1); editing = null; await savePlaylists(); }), "danger")));
  })));
  renderEditor();
  renderSchedule();
}

function renderEditor() {
  const pl = playlists[current];
  const box = $("plEditor");
  if (editing === null || !pl?.items[editing]) return box.replaceChildren();
  const item = pl.items[editing];
  const app = catalog.byId[item.app];
  const form = paramForm(app, item.params);
  box.replaceChildren(
    el("h3", {}, `Edit: ${app.title}`), form.el,
    el("div", { class: "row" },
      el("button", { class: "primary", onclick: safe(async () => { item.params = form.values(); editing = null; await savePlaylists(); toast("Saved"); }) }, "Save item"),
      el("button", { onclick: () => { editing = null; render(); } }, "Cancel")));
}

function renderSchedule() {
  $("schEnabled").checked = !!schedule.enabled;
  const opts = sel => [el("option", { value: "", selected: !sel }, "(nothing)"),
    el("option", { value: OFF, selected: sel === OFF }, "Off (all black)"),
    ...Object.keys(playlists).map(n => el("option", { value: n, selected: n === sel }, n))];
  $("schDefault").replaceChildren(...opts(schedule.default));
  $("schRules").replaceChildren(el("div", { class: "items" }, schedule.rules.map((rule, i) => {
    rule.days ??= [0, 1, 2, 3, 4, 5, 6];
    const start = el("input", { type: "time", value: rule.start || "00:00" });
    const end = el("input", { type: "time", value: rule.end || "23:59" });
    const target = el("select", {}, opts(rule.playlist));
    start.onchange = safe(() => { rule.start = start.value; return saveSchedule(); });
    end.onchange = safe(() => { rule.end = end.value; return saveSchedule(); });
    target.onchange = safe(() => { rule.playlist = target.value; return saveSchedule(); });
    return el("div", { class: "item" },
      el("div", { class: "days" }, DAYS.map((d, di) => el("button", {
        class: rule.days.includes(di) ? "active" : "",
        onclick: safe(() => {
          rule.days = rule.days.includes(di) ? rule.days.filter(x => x !== di) : [...rule.days, di].sort();
          return saveSchedule();
        }),
      }, d))),
      start, el("span", { class: "muted" }, "to"), end, el("span", { class: "muted" }, "play"), target,
      iconButton("x", "Remove rule", safe(() => { schedule.rules.splice(i, 1); return saveSchedule(); }), "danger"));
  })));
}

$("plSelect").onchange = () => { current = $("plSelect").value; editing = null; render(); };
$("plTransition").onchange = safe(async () => { playlists[current].transition = $("plTransition").value; await savePlaylists(); });
$("plTransDur").onchange = safe(async () => { playlists[current].transition_duration = Math.max(0.1, +$("plTransDur").value || 1); await savePlaylists(); });
$("plNew").onclick = safe(async () => {
  const name = prompt("Playlist name?");
  if (!name) return;
  if (playlists[name]) return toast("That name is taken", true);
  playlists[name] = { transition: "dissolve", items: [] };
  current = name;
  await savePlaylists();
});
$("plRename").onclick = safe(async () => {
  if (!current) return;
  const name = prompt("New name?", current);
  if (!name || name === current) return;
  playlists[name] = playlists[current];
  delete playlists[current];
  if (schedule.default === current) schedule.default = name;
  schedule.rules.forEach(r => { if (r.playlist === current) r.playlist = name; });
  current = name;
  await savePlaylists();
  await saveSchedule();
});
$("plDelete").onclick = safe(async () => {
  if (!current || !confirm(`Delete playlist "${current}"?`)) return;
  delete playlists[current];
  current = Object.keys(playlists)[0] || null;
  await savePlaylists();
});
$("plPlay").onclick = safe(async () => { if (current) { await api("/api/playlist/play", { name: current }); toast(`Playing "${current}"`); } });
$("plAdd").onclick = safe(async () => {
  if (!current) return toast("Create a playlist first", true);
  const app = catalog.byId[$("plAddApp").value];
  playlists[current].items.push({ app: app.id, params: paramForm(app).values(), duration: 30 });
  editing = playlists[current].items.length - 1;
  await savePlaylists();
});
$("schEnabled").onchange = safe(() => { schedule.enabled = $("schEnabled").checked; return saveSchedule(); });
$("schDefault").onchange = safe(() => { schedule.default = $("schDefault").value; return saveSchedule(); });
$("schAdd").onclick = safe(() => {
  schedule.rules.push({ days: [0, 1, 2, 3, 4], start: "07:00", end: "09:00", playlist: Object.keys(playlists)[0] || "" });
  return saveSchedule();
});
bus.addEventListener("scenes", () => render());
