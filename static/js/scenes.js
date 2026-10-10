// Scenes tab: saved drawings, images and animations.
import { $, api, bus, download, el, safe, scenesCache, thumbCanvas, toast, refreshScenes, renderDots, rowsToGrid } from "./common.js";

function render() {
  const grid = $("sceneGrid");
  if (!scenesCache.list.length) {
    grid.replaceChildren(el("p", { class: "muted" }, "No scenes yet. Save one from the Draw or Image & video tab."));
    return;
  }
  grid.replaceChildren(...scenesCache.list.map(s => el("div", { class: "scene" },
    thumbCanvas(s.thumb, 3),
    el("div", { class: "name" }, el("span", {}, s.name), el("span", { class: "muted" }, s.frames > 1 ? `${s.frames} frames` : "")),
    el("div", { class: "row" },
      el("button", { class: "primary", onclick: safe(async () => {
        await api("/api/run", { app: "scene", params: { scene: s.id } });
        toast(`Showing "${s.name}"`);
      }) }, "Show"),
      el("button", { title: "Add on top of what's on the board", onclick: safe(async () => {
        await api("/api/layers/add", { layer: { app: "scene", params: { scene: s.id } }, position: "top" });
        toast(`Added "${s.name}" as a layer`);
      }) }, "+ Layer"),
      el("button", { title: "Open in the Draw tab's animation editor", onclick: () => bus.dispatchEvent(new CustomEvent("editScene", { detail: s.id })) }, "Edit"),
      el("button", { onclick: safe(async () => {
        const full = await api(`/api/scenes/${s.id}`);
        const c = document.createElement("canvas");
        c.width = 84 * 12; c.height = 42 * 12;
        renderDots(c.getContext("2d"), rowsToGrid(full.frames[0].rows), 12, { seams: false });
        c.toBlob(b => download(`${s.name}.png`, b));
      }) }, "PNG"),
      el("button", { onclick: safe(async () => {
        const full = await api(`/api/scenes/${s.id}`);
        download(`${s.name}.json`, new Blob([JSON.stringify(full)], { type: "application/json" }));
      }) }, "JSON"),
      el("button", { onclick: safe(async () => {
        const name = prompt("New name?", s.name);
        if (!name) return;
        const full = await api(`/api/scenes/${s.id}`);
        await api("/api/scenes", { id: s.id, name, frames: full.frames });
        await refreshScenes();
      }) }, "Rename"),
      el("button", { class: "danger", onclick: safe(async () => {
        if (!confirm(`Delete "${s.name}"?`)) return;
        await api(`/api/scenes/${s.id}/delete`, {});
        await refreshScenes();
      }) }, "Delete")))));
}

$("scSaveCurrent").onclick = safe(async () => {
  const name = prompt("Scene name?", "Board " + new Date().toLocaleString());
  if (!name) return;
  await api("/api/scenes", { name });
  await refreshScenes();
  toast(`Saved "${name}"`);
});
bus.addEventListener("scenes", render);
