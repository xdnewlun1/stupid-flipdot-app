// Playlists and Schedule pages.
import { $, api, appOptions, ask, autosave, bus, catalog, confirmDelete, describeItem, el, flushUndo, iconButton, nameFree, paramForm, safe, toast, undoable } from "./common.js";

let playlists = {}, current = null, editing = null;
let draft = null;      // an item being added: it joins the playlist only on "Add to playlist"
let warnings = {};     // playlist -> { item index: "needs setup" message }
let schedule = { enabled: false, default: "", rules: [] }, OFF = "__off__";
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const playlistNames = () => Object.keys(playlists);

export async function loadPlaylists() {
  const j = await api("/api/playlists");
  playlists = j.playlists; warnings = j.warnings || {};
  const s = await api("/api/schedule");
  schedule = s.schedule; OFF = s.off;
  if (!current || !playlists[current]) current = Object.keys(playlists)[0] || null;
  render();
}

// keepEditor: the item editor stays as it is (it's saving what you type), only the list redraws
async function savePlaylists({ keepEditor = false } = {}) {
  const j = await autosave("plSaved", () => api("/api/playlists", { playlists }));
  playlists = j.playlists; warnings = j.warnings || {};
  bus.dispatchEvent(new Event("playlists"));
  keepEditorOpen = keepEditor;
  try { render(); } finally { keepEditorOpen = false; }
}
let keepEditorOpen = false;

async function saveSchedule() {
  schedule = (await autosave("schSaved", () => api("/api/schedule", { schedule }))).schedule;
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
  if (!$("plAddApp").options.length) $("plAddApp").replaceChildren(...appOptions());
  const pl = playlists[current];
  const box = $("plItems");
  for (const id of ["plRename", "plDelete", "plPlay", "plAdd"]) $(id).disabled = !pl;
  if (!pl) {
    box.replaceChildren(el("div", { class: "empty" }, el("p", { class: "muted" }, "No playlists yet. A playlist shows apps and pictures one after another."),
      el("button", { class: "primary", onclick: () => $("plNew").click() }, "New playlist")));
    renderSchedule(); return;
  }
  if (!pl.items.length) box.replaceChildren(el("p", { class: "muted" }, "This playlist is empty. Pick an app below and press Add item, or use Add to playlist on the Apps page."));
  else box.replaceChildren(el("div", { class: "items" }, pl.items.map((item, i) => {
    const { title, detail } = describeItem(item);
    const warn = warnings[current]?.[i];
    const dur = el("input", { type: "number", min: 1, value: item.duration, title: "Seconds on the board", "aria-label": "Seconds on the board" });
    const trans = el("select", { title: "Transition into this item", "aria-label": "Transition into this item" },
      el("option", { value: "" }, "Playlist transition"),
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
      el("div", { class: "name" }, title, el("small", {}, detail),
        warn ? el("small", { class: "warnText" }, `Needs setup: ${warn}. It's skipped until then.`) : null),
      el("label", { class: "muted" }, dur, " s"), trans,
      el("div", { class: "group" },
        iconButton("up", "Move up", move(-1)),
        iconButton("down", "Move down", move(1)),
        iconButton("edit", editing === i ? "Close editor" : "Edit settings", () => { editing = editing === i ? null : i; draft = null; render(); }),
        iconButton("play", "Show this item on the board now", safe(() => api("/api/run", { app: item.app, params: item.params }))),
        iconButton("x", "Remove from playlist", safe(async () => {
          const [gone] = pl.items.splice(i, 1);
          editing = null;
          await savePlaylists();
          const name = current;  // saving swaps in fresh objects from the server, so look the playlist up again
          undoable(`Removed ${title}`, { undo: async () => { playlists[name]?.items.splice(i, 0, gone); await savePlaylists(); } });
        }), "danger")));
  })));
  renderEditor();
  renderSchedule();
}

function renderEditor() {
  if (keepEditorOpen) return;
  const pl = playlists[current];
  const box = $("plEditor");
  const item = draft || (editing !== null && pl?.items[editing]);
  if (!item) return box.replaceChildren();
  const app = catalog.byId[item.app];
  const form = paramForm(app, item.params);
  if (draft) {
    // a new item joins the playlist only when you add it
    box.replaceChildren(el("h3", {}, `New item: ${app.title}`), form.el,
      el("div", { class: "row" },
        el("button", { class: "primary", onclick: safe(async () => {
          draft.params = form.values();
          pl.items.push(draft); draft = null;
          await savePlaylists();
          toast(`Added ${app.title}`);
        }) }, "Add to playlist"),
        el("button", { onclick: () => { draft = null; render(); } }, "Cancel")));
    return;
  }
  // an item that's already in the playlist saves itself as you change it
  let timer = null;
  const name = current, index = editing;
  const changed = () => {
    clearTimeout(timer);
    timer = setTimeout(safe(async () => {
      const live = playlists[name]?.items[index];  // saving swaps in fresh objects from the server
      if (!live || editing !== index || current !== name) return;
      live.params = form.values();
      await savePlaylists({ keepEditor: true });
    }), 400);
  };
  form.el.addEventListener("input", changed);
  form.el.addEventListener("change", changed);
  box.replaceChildren(el("h3", {}, `Edit: ${app.title}`), form.el,
    el("div", { class: "row" },
      el("button", { onclick: () => { editing = null; render(); } }, "Done"),
      el("span", { class: "muted" }, "Changes save as you make them.")));
}

function renderSchedule() {
  $("schEnabled").checked = !!schedule.enabled;
  const opts = sel => [el("option", { value: "", selected: !sel }, "Nothing"),
    el("option", { value: OFF, selected: sel === OFF }, "Off (all black)"),
    ...Object.keys(playlists).map(n => el("option", { value: n, selected: n === sel }, n))];
  $("schDefault").replaceChildren(...opts(schedule.default));
  if (!schedule.rules.length) return $("schRules").replaceChildren(el("p", { class: "muted" }, "No rules yet, so the default playlist plays all day. Press Add rule for time-based changes."));
  $("schRules").replaceChildren(el("div", { class: "items" }, schedule.rules.map((rule, i) => {
    rule.days ??= [0, 1, 2, 3, 4, 5, 6];
    const start = el("input", { type: "time", value: rule.start || "00:00", "aria-label": "From" });
    const end = el("input", { type: "time", value: rule.end || "23:59", "aria-label": "Until" });
    const target = el("select", { "aria-label": "Playlist" }, opts(rule.playlist));
    start.onchange = safe(() => { rule.start = start.value; return saveSchedule(); });
    end.onchange = safe(() => { rule.end = end.value; return saveSchedule(); });
    target.onchange = safe(() => { rule.playlist = target.value; return saveSchedule(); });
    return el("div", { class: "item" },
      el("div", { class: "days" }, DAYS.map((d, di) => el("button", {
        class: rule.days.includes(di) ? "active" : "", "aria-pressed": String(rule.days.includes(di)),
        onclick: safe(() => {
          rule.days = rule.days.includes(di) ? rule.days.filter(x => x !== di) : [...rule.days, di].sort();
          return saveSchedule();
        }),
      }, d))),
      start, el("span", { class: "muted" }, "to"), end, el("span", { class: "muted" }, "play"), target,
      iconButton("x", "Remove rule", safe(async () => {
        const [gone] = schedule.rules.splice(i, 1);
        await saveSchedule();
        undoable("Removed the rule", { undo: async () => { schedule.rules.splice(i, 0, gone); await saveSchedule(); } });  // `schedule` is the live one
      }), "danger"));
  })));
}

$("plSelect").onchange = () => { flushUndo(); current = $("plSelect").value; editing = null; draft = null; render(); };
$("plTransition").onchange = safe(async () => { playlists[current].transition = $("plTransition").value; await savePlaylists(); });
$("plTransDur").onchange = safe(async () => { playlists[current].transition_duration = Math.max(0.1, +$("plTransDur").value || 1); await savePlaylists(); });
$("plNew").onclick = safe(async () => {
  const name = await ask({ title: "New playlist", label: "Name", confirmText: "Create", validate: nameFree(Object.keys(playlists)) });
  if (!name) return;
  playlists[name] = { transition: "dissolve", items: [] };
  current = name;
  await savePlaylists();
});
$("plRename").onclick = safe(async () => {
  if (!current) return;
  const name = await ask({ title: "Rename playlist", label: "Name", value: current, confirmText: "Rename", validate: nameFree(Object.keys(playlists), current) });
  if (!name || name === current) return;
  flushUndo();
  // the server updates the schedule and automations that use the old name too
  const j = await api("/api/playlists/rename", { old: current, new: name });
  current = name;
  await loadPlaylists();
  bus.dispatchEvent(new Event("playlists"));
  bus.dispatchEvent(new Event("refs"));
  toast(j.updated ? `Renamed. Updated ${j.updated} place${j.updated === 1 ? "" : "s"} that used it.` : "Renamed");
});
$("plDelete").onclick = safe(async () => {
  if (!current || !await confirmDelete("playlist", current, `the playlist "${current}"`)) return;
  flushUndo();
  await api("/api/playlists/delete", { name: current });
  current = null;
  await loadPlaylists();
  bus.dispatchEvent(new Event("playlists"));
  bus.dispatchEvent(new Event("refs"));
});
$("plPlay").onclick = safe(async () => { if (current) { await api("/api/playlist/play", { name: current }); toast(`Playing "${current}"`); } });
$("plAdd").onclick = safe(async () => {
  if (!current) return toast("Create a playlist first", true);
  const app = catalog.byId[$("plAddApp").value];
  draft = { app: app.id, params: paramForm(app).values(), duration: 30 };
  editing = null;
  render();
  $("plEditor").scrollIntoView({ block: "nearest" });
});
$("schEnabled").onchange = safe(() => { schedule.enabled = $("schEnabled").checked; return saveSchedule(); });
$("schDefault").onchange = safe(() => { schedule.default = $("schDefault").value; return saveSchedule(); });
$("schAdd").onclick = safe(() => {
  schedule.rules.push({ days: [0, 1, 2, 3, 4], start: "07:00", end: "09:00", playlist: Object.keys(playlists)[0] || "" });
  return saveSchedule();
});
bus.addEventListener("scenes", () => render());
bus.addEventListener("refs", () => loadPlaylists().catch(e => toast(e.message, true)));
