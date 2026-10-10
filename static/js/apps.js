// Apps tab: browse apps, set options (live for the app you started), show on board / for N s / as a layer / in a playlist.
import { $, api, appSections, autosave, board, catalog, el, icon, iconButton, loadNesRoms, nesCache, paramForm, safe, toast, bus, undoable } from "./common.js";
import { addToPlaylist, playlistNames } from "./playlists.js";

let selected = null, form = null;
let filter = "all", query = "";
try { filter = sessionStorage.getItem("appFilter") || "all"; } catch {}

const PLAYLIST_NOTE = "Each of these shows something you made: a picture, a drawing layer, a layer set or a slideshow. "
  + "You can also show those straight from their own pages.";

const card = a => el("button", { class: "appCard" + (selected?.id === a.id ? " active" : ""), "aria-pressed": selected?.id === a.id ? "true" : "false",
  onclick: () => select(a, { reveal: true }) }, a.title, el("small", {}, a.description));

export function renderAppList() {
  const sections = appSections();
  // category chips: one at a time, "All" shows everything
  $("appChips").replaceChildren(...[["all", "All"], ...sections.map(s => [s.key, s.label])].map(([key, label]) =>
    el("button", { "aria-pressed": String(filter === key), onclick: () => { filter = key; try { sessionStorage.setItem("appFilter", key); } catch {} renderAppList(); } }, label)));
  const q = query.trim().toLowerCase();
  const match = a => !q || `${a.title} ${a.description} ${a.category} ${a.id}`.toLowerCase().includes(q);
  const shown = sections.filter(s => filter === "all" || s.key === filter || q)
    .map(s => ({ ...s, apps: s.apps.filter(match) })).filter(s => s.apps.length);
  const list = $("appList");
  if (!shown.length) {
    list.replaceChildren(el("div", { class: "empty" }, el("p", { class: "muted" }, `No apps match "${query.trim()}".`),
      el("button", { onclick: () => { query = $("appSearch").value = ""; renderAppList(); $("appSearch").focus(); } }, "Clear the search")));
    return;
  }
  list.replaceChildren(...shown.map(s => el("div", { class: "appCat" },
    el("h3", {}, s.label, el("span", { class: "catCount" }, ` ${s.apps.length}`)),
    s.key === "playlists" ? el("p", { class: "muted" }, PLAYLIST_NOTE, " ",
      el("a", { href: "#pictures" }, "Pictures"), ", ", el("a", { href: "#layers" }, "Layers"), ", ", el("a", { href: "#slideshows" }, "Slideshows"), ".") : null,
    s.key === "games" && !q
      ? Object.entries(s.groups).map(([g, apps]) => el("div", { class: "gameGroup" }, el("h4", {}, g), el("div", { class: "appCards" }, apps.filter(match).map(card))))
      : el("div", { class: "appCards" }, s.apps.map(card)))));
}
$("appSearch").addEventListener("input", () => { query = $("appSearch").value; renderAppList(); });
$("appSearch").addEventListener("keydown", e => {
  if (e.key === "Escape") { query = $("appSearch").value = ""; renderAppList(); }
  if (e.key === "Enter") $("appList").querySelector(".appCard")?.click();
});

// the app you started yourself is "live": changing its options updates the board
const isRunning = app => board.runner?.app === app.id && !board.runner.notification;
const isLive = app => isRunning(app) && board.runner.mode === "app";
const noteText = app => isLive(app) ? "On the board now. Changes show there right away."
  : isRunning(app) ? "On the board now, from the schedule or a playlist. Show on board to use these options instead (that pauses the schedule)." : "";

function select(app, { reveal = false } = {}) {
  selected = app;
  renderAppList();
  // open with what's on the board when this app is running, so a change starts from there
  const running = isRunning(app) && board.runner.spec?.app === app.id ? board.runner.spec.params : {};
  form = paramForm(app, running);
  const transition = el("select", { "aria-label": "Transition" }, catalog.transitions.map(t => el("option", { value: t }, t)));
  const tDur = el("input", { type: "number", value: 1, min: 0.1, max: 10, step: 0.1, inputmode: "decimal", style: "width:64px", "aria-label": "Transition seconds" });
  const duration = el("input", { type: "number", value: 30, min: 1, style: "width:80px", "aria-label": "Seconds in the playlist" });
  const notifySecs = el("input", { type: "number", value: 10, min: 1, style: "width:70px", "aria-label": "Seconds to show it" });
  const plSelect = el("select", { "aria-label": "Playlist" }, playlistNames().map(n => el("option", { value: n }, n)));
  const spec = () => ({ app: app.id, params: form.values() });
  const liveNote = el("p", { class: "muted liveNote", role: "status" });
  const updateNote = () => { liveNote.textContent = noteText(app); };
  updateNote();
  liveNote.dataset.app = app.id;

  // live options: a short pause after the last change, then the running app restarts with them
  let timer = null;
  const pushLive = () => {
    if (!isLive(app)) return;
    clearTimeout(timer);
    timer = setTimeout(safe(async () => {
      if (!isLive(app) || selected !== app) return;
      await autosave(liveNote, () => api("/api/params", spec()), "Updated on the board");
    }), 350);
  };
  form.el.addEventListener("input", pushLive);
  form.el.addEventListener("change", pushLive);

  // the main action sits right under the title, so it's on screen without scrolling; the options follow
  $("appDetail").replaceChildren(...[
    el("h2", {}, app.title),
    el("p", { class: "muted" }, app.description),
    el("div", { class: "row" },
      el("button", { class: "primary", onclick: safe(async () => {
        await api("/api/run", { ...spec(), transition: transition.value, transition_duration: +tDur.value || 1 });
        toast(`Showing ${app.title}`);
        updateNote();
      }) }, icon("play"), "Show on board"),
      el("label", { class: "muted" }, "with ", transition), el("label", { class: "muted" }, tDur, " s")),
    liveNote,
    app.interactive ? el("p", {}, "Control it from your phone at ", el("a", { href: "/play", target: "_blank" }, location.host + "/play"),
      ", or with the arrow keys / WASD + Space / Enter on this page while it runs.") : null,
    app.params.length ? form.el : el("p", { class: "muted" }, "No options."),
    app.id === "nes" ? nesPanel(form) : null,
    el("h3", {}, "Other ways to show it"),
    el("div", { class: "row" },
      el("button", { onclick: safe(async () => {
        await api("/api/notify", { ...spec(), duration: +notifySecs.value });
        toast(`Showing ${app.title} for ${notifySecs.value} s`);
      }) }, "Show for"),
      el("label", { class: "muted" }, notifySecs, " s, then go back")),
    app.id === "layers" ? null : el("div", { class: "row" },
      el("button", { title: "Put it on top of what's on the board now", onclick: safe(async () => {
        await api("/api/layers/add", { layer: { app: app.id, params: form.values() }, position: "top" });
        toast(`Added ${app.title} as a layer on top`);
      }) }, "Add as layer"), el("span", { class: "muted" }, "on top of what's on the board")),
    el("div", { class: "row" },
      el("button", { onclick: safe(async () => {
        if (!plSelect.value) return toast("Create a playlist first", true);
        await addToPlaylist(plSelect.value, { ...spec(), duration: +duration.value });
        toast(`Added to "${plSelect.value}"`);
      }) }, "Add to playlist"),
      plSelect, el("label", { class: "muted" }, "for ", duration, " s")),
  ].filter(Boolean));
  // on narrow screens the details sit below the long list: bring them into view
  if (reveal && window.matchMedia("(max-width: 1100px)").matches)
    $("appDetail").scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

// keep the "on the board now" note right as things start and stop
let lastRun = "";
bus.addEventListener("state", () => {
  const r = board.runner, key = r ? `${r.mode}|${r.app}|${r.notification}` : "";
  if (key === lastRun || !selected) return;
  lastRun = key;
  const note = $("appDetail").querySelector(".liveNote");
  if (note?.dataset.app === selected.id) note.textContent = noteText(selected);
});

bus.addEventListener("playlists", () => { if (selected) select(selected); });
bus.addEventListener("scenes", () => { if (selected?.params.some(p => p.type === "scene")) select(selected); });
bus.addEventListener("compositions", () => { if (selected?.id === "layers") select(selected); });
bus.addEventListener("fonts", () => { if (selected?.params.some(p => p.type === "font")) select(selected); });

// ---- NES: the uploaded ROMs, under the NES app's options ----
function nesPanel(form) {
  const note = el("span", { class: "savedNote", role: "status" });
  const warn = el("p", { class: "muted", hidden: true });
  const list = el("div", { class: "items" });
  const removed = new Set();  // deleted, until the Undo window runs out
  const file = el("input", { type: "file", accept: ".nes", multiple: true, hidden: true, onchange: safe(async e => {
    const files = [...e.target.files];
    e.target.value = "";
    let last = null;
    for (const f of files) {
      if (f.size > (nesCache.max_bytes || 1049104)) throw new Error(`${f.name} is ${Math.round(f.size / 1024)} KB; the limit is 1 MB`);
      last = await autosave(note, () => api(`/api/nes/roms?name=${encodeURIComponent(f.name)}`, f, { raw: true }), "Uploaded");
    }
    await loadNesRoms();
    if (last) toast(files.length > 1 ? `Added ${files.length} games` : `${last.replaced ? "Replaced" : "Added"} ${last.name.replace(/\.nes$/i, "")}`);
  }) });
  const draw = () => {
    if (!list.isConnected && list.dataset.drawn) return bus.removeEventListener("nesroms", draw);
    list.dataset.drawn = "1";
    warn.hidden = nesCache.built;
    if (!nesCache.built) warn.replaceChildren(icon("warn"), " The emulator isn't built on the server yet: run scripts/setup-nes.sh there, then reload this page.");
    const roms = nesCache.list.filter(r => !removed.has(r.name));
    list.replaceChildren(...(roms.length ? roms.map(r => el("div", { class: "item" },
      el("span", { class: "name" }, r.title, el("small", { class: r.problem ? "warnText" : "" },
        r.problem || `${r.mapper_name} · ${Math.round(r.size / 1024)} KB${r.saved ? " · has a saved game" : ""}`)),
      r.problem ? null : el("button", { onclick: safe(async () => {
        await api("/api/run", { app: "nes", params: { ...form.values(), rom: r.name } });
        toast(`Showing ${r.title}`);
      }) }, icon("play"), "Show on board"),
      iconButton("x", `Delete ${r.title}`, () => {
        removed.add(r.name);
        draw();
        undoable(`Deleted ${r.title}${r.saved ? " and its saved game" : ""}`, {
          undo: () => { removed.delete(r.name); draw(); },
          commit: async () => {
            try { await api("/api/nes/roms/delete", { name: r.name }); } finally { removed.delete(r.name); await loadNesRoms(); }
          },
        });
      })))
      : [el("p", { class: "muted" }, nesCache.loaded ? "No games yet. Upload a .nes file to play." : "Loading…")]));
  };
  bus.addEventListener("nesroms", draw);
  loadNesRoms().catch(e => toast(e.message, true));
  const box = el("div", { class: "nesRoms" },
    el("h3", {}, "NES ROMs", note),
    el("p", { class: "muted" }, "No games come with the board: you must supply your own .nes files, from cartridges you own or free "
      + "homebrew. Up to 1 MB each. Games on the common NROM, MMC1, UxROM, CNROM, MMC3 and AxROM boards run; the game you pick "
      + "above starts with Show on board."),
    warn,
    el("div", { class: "row" }, el("button", { onclick: () => file.click() }, icon("upload"), "Upload ROM…"), file),
    list);
  draw();
  return box;
}
