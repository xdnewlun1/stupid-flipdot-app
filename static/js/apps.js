// Apps tab: browse apps, set options, run now / notify / add to a playlist.
import { $, api, catalog, el, paramForm, safe, toast, bus } from "./common.js";
import { addToPlaylist, playlistNames } from "./playlists.js";

let selected = null, form = null;

export function renderAppList() {
  const list = $("appList");
  list.innerHTML = "";
  const cats = {};
  for (const a of catalog.apps) (cats[a.category] ||= []).push(a);
  for (const [cat, items] of Object.entries(cats)) {
    list.append(el("div", { class: "appCat" },
      el("h3", {}, cat),
      el("div", { class: "appCards" }, items.map(a =>
        el("button", { class: "appCard" + (selected?.id === a.id ? " active" : ""), "aria-pressed": selected?.id === a.id ? "true" : "false", onclick: () => select(a, { reveal: true }) },
          a.title, el("small", {}, a.description))))));
  }
}

function select(app, { reveal = false } = {}) {
  selected = app;
  renderAppList();
  form = paramForm(app);
  const transition = el("select", {}, catalog.transitions.map(t => el("option", { value: t }, t)));
  const tDur = el("input", { type: "number", value: 1, min: 0.1, max: 10, step: 0.1, style: "width:64px" });
  const duration = el("input", { type: "number", value: 30, min: 1, style: "width:80px" });
  const notifySecs = el("input", { type: "number", value: 10, min: 1, style: "width:70px" });
  const plSelect = el("select", {}, playlistNames().map(n => el("option", { value: n }, n)));
  const spec = () => ({ app: app.id, params: form.values() });

  $("appDetail").replaceChildren(...[
    el("h2", {}, app.title),
    el("p", { class: "muted" }, app.description),
    app.interactive ? el("p", {}, "Control it from your phone at ", el("a", { href: "/play", target: "_blank" }, location.host + "/play"),
      ", or with the arrow keys / WASD + Space / Enter on this page while it runs.") : null,
    app.params.length ? form.el : el("p", { class: "muted" }, "No options."),
    el("div", { class: "row" },
      el("button", { class: "primary", onclick: safe(async () => {
        await api("/api/run", { ...spec(), transition: transition.value, transition_duration: +tDur.value || 1 });
        toast(`Running ${app.title}`);
      }) }, "Run now"),
      el("label", { class: "muted" }, "transition ", transition), el("label", { class: "muted" }, tDur, " s")),
    el("div", { class: "row" },
      el("button", { onclick: safe(async () => {
        await api("/api/notify", { ...spec(), duration: +notifySecs.value });
        toast(`Showing ${app.title} for ${notifySecs.value}s`);
      }) }, "Show briefly"),
      el("label", { class: "muted" }, "for ", notifySecs, " s, then go back")),
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

bus.addEventListener("playlists", () => { if (selected) select(selected); });
bus.addEventListener("scenes", () => { if (selected?.params.some(p => p.type === "scene")) select(selected); });
bus.addEventListener("compositions", () => { if (selected?.id === "layers") select(selected); });
bus.addEventListener("fonts", () => { if (selected?.params.some(p => p.type === "font")) select(selected); });
