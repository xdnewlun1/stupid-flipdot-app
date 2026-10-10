// Pictures page: one library for saved pictures and animations (scenes) and drawing layers (drawings).
import {
  $, api, ask, board, bus, confirmDelete, download, drawingsCache, el, icon, loadDrawings, refreshScenes, renderDots, rowsToGrid,
  safe, scenesCache, thumbCanvas, toast,
} from "./common.js";

let kind = "all";
let fresh = new Set(), adjustable = null;  // just imported (highlighted until another page opens); the one Import can still re-convert

// one shape for both: what it is, and what each button does with it
function pictures() {
  const onBoard = new Set(board.runner?.drawings || []);
  const scenes = scenesCache.list.map(s => ({
    key: "s" + s.id, name: s.name, thumb: s.thumb, kind: s.frames > 1 ? "anim" : "still",
    meta: s.frames > 1 ? `Animation · ${s.frames} frames` : "Picture",
    show: () => api("/api/run", { app: "scene", params: { scene: s.id } }),
    layer: () => api("/api/layers/add", { layer: { app: "scene", params: { scene: s.id } }, position: "top" }),
    edit: () => bus.dispatchEvent(new CustomEvent("editScene", { detail: s.id })),
    rename: async name => {
      const full = await api(`/api/scenes/${s.id}`);
      await api("/api/scenes", { id: s.id, name, frames: full.frames });
      await refreshScenes();
    },
    remove: async () => {
      if (!await confirmDelete("scene", s.id, `"${s.name}"`)) return;
      await api(`/api/scenes/${s.id}/delete`, {});
      await refreshScenes();
      bus.dispatchEvent(new Event("refs"));
    },
    files: [
      ["PNG", async () => {
        const full = await api(`/api/scenes/${s.id}`);
        const c = document.createElement("canvas");
        c.width = 84 * 12; c.height = 42 * 12;
        renderDots(c.getContext("2d"), rowsToGrid(full.frames[0].rows), 12, { seams: false });
        c.toBlob(b => download(`${s.name}.png`, b));
      }],
      ["JSON", async () => {
        const full = await api(`/api/scenes/${s.id}`);
        download(`${s.name}.json`, new Blob([JSON.stringify(full)], { type: "application/json" }));
      }],
    ],
  }));
  const drawings = drawingsCache.list.map(d => ({
    key: "d" + d.id, name: d.name, thumb: d.thumb, kind: "drawing", live: onBoard.has(d.id),
    meta: onBoard.has(d.id) ? "Drawing layer · on the board now" : "Drawing layer",
    show: () => api("/api/run", { app: "drawing", params: { drawing: d.id } }),
    layer: onBoard.has(d.id) ? null : () => api(`/api/drawings/${d.id}/show`, {}),
    edit: () => bus.dispatchEvent(new CustomEvent("drawTarget", { detail: d.id })),
    rename: async name => { await api(`/api/drawings/${d.id}`, { name }); await loadDrawings(); },
    remove: async () => {
      if (!await confirmDelete("drawing", d.id, `the drawing layer "${d.name}"`)) return;
      await api(`/api/drawings/${d.id}/delete`, {});
      await loadDrawings();
      bus.dispatchEvent(new Event("refs"));
    },
    files: [],
  }));
  return [...scenes, ...drawings];
}

function pictureCard(p) {
  const isNew = fresh.has(p.key);
  return el("div", { class: "scene" + (p.live ? " live" : "") + (isNew ? " justAdded" : ""), "data-key": p.key },
    p.thumb ? thumbCanvas(p.thumb, 3) : el("div", { class: "noThumb" }),
    el("div", { class: "name" }, el("span", {}, p.name), el("small", {}, isNew ? `Just imported · ${p.meta}` : p.meta)),
    el("div", { class: "row" },
      el("button", { class: "primary", onclick: safe(async () => { await p.show(); toast(`Showing "${p.name}"`); }) }, icon("play"), "Show on board"),
      p.layer ? el("button", { title: "Add it on top of what's on the board", onclick: safe(async () => { await p.layer(); toast(`Added "${p.name}" as a layer`); }) }, "Add as layer") : null,
      el("button", { title: "Open it in Draw", onclick: p.edit }, icon("edit"), "Edit"),
      adjustable === p.key ? el("button", { title: "Back to Import: change sizing, style, cutoff or negative, and this picture is updated",
        onclick: () => bus.dispatchEvent(new CustomEvent("tab", { detail: "import" })) }, "Change conversion") : null),
    el("div", { class: "row minor" },
      el("button", { class: "ghost", onclick: safe(async () => {
        const name = await ask({ title: "Rename", label: "Name", value: p.name, confirmText: "Rename" });
        if (name && name !== p.name) await p.rename(name);
      }) }, "Rename"),
      ...p.files.map(([label, fn]) => el("button", { class: "ghost", title: `Download as ${label}`, onclick: safe(fn) }, label)),
      el("span", { class: "spacer" }),
      el("button", { class: "ghost danger", onclick: safe(p.remove) }, "Delete")));
}

function render() {
  document.querySelectorAll("#pcFilter [data-kind]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.kind === kind)));
  const all = pictures();
  const list = all.filter(p => kind === "all" || p.kind === kind);
  const grid = $("sceneGrid");
  if (!all.length) {
    grid.replaceChildren(el("div", { class: "empty wide" },
      el("p", {}, "No pictures yet."),
      el("p", { class: "muted" }, "Draw one, import an image or a GIF, or save what's on the board right now. They all land here, ready to show, add as a layer or put in a playlist.")));
    return;
  }
  if (!list.length) {
    grid.replaceChildren(el("div", { class: "empty wide" }, el("p", { class: "muted" }, "Nothing of this kind yet."),
      el("button", { onclick: () => { kind = "all"; render(); } }, "Show all pictures")));
    return;
  }
  grid.replaceChildren(...list.map(pictureCard));
}

// From Import: show the new pictures highlighted, the first one scrolled into view
bus.addEventListener("picturesAdded", e => {
  fresh = new Set(e.detail.ids.map(id => "s" + id));
  adjustable = e.detail.adjust ? "s" + e.detail.adjust : null;
  kind = "all";
  render();
  const first = document.querySelector(`#sceneGrid [data-key="s${CSS.escape(e.detail.ids[0])}"]`);
  if (first) {
    first.scrollIntoView({ block: "center", behavior: "smooth" });
    first.querySelector("button.primary")?.focus({ preventScroll: true });
  }
});
bus.addEventListener("importReset", () => { adjustable = null; });

$("pcFilter").addEventListener("click", e => { const b = e.target.closest("[data-kind]"); if (b) { kind = b.dataset.kind; render(); } });
$("scSaveCurrent").onclick = safe(async () => {
  const name = await ask({ title: "Save what's on the board", label: "Name", value: "Board " + new Date().toLocaleString(), confirmText: "Save" });
  if (!name) return;
  await api("/api/scenes", { name });
  await refreshScenes();
  toast(`Saved "${name}" to Pictures`);
});
$("pcDraw").onclick = () => bus.dispatchEvent(new CustomEvent("tab", { detail: "draw" }));
$("pcImport").onclick = () => { bus.dispatchEvent(new CustomEvent("tab", { detail: "import" })); $("file").click(); };
bus.addEventListener("scenes", render);
bus.addEventListener("drawings", render);
// "on the board now" follows the board
let liveKey = "";
bus.addEventListener("state", () => {
  const k = (board.runner?.drawings || []).join();
  if (k !== liveKey) { liveKey = k; if (!$("tab-pictures").hidden) render(); }
});
bus.addEventListener("tabShown", e => {
  if (e.detail === "pictures") render();
  else if (fresh.size) fresh = new Set();
});
