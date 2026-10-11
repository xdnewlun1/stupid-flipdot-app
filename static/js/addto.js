// "Add to playlist" from any page: one dialog to pick a playlist (or start a new one) and how long the item
// stays on the board, then "Added" with a link to the playlist. Pages call addToPlaylist(item, {name}) or put
// addToPlaylistButton(...) next to their Show on board.
import { api, bus, el, icon, safe, toast } from "./common.js";

const NEW = "\u0000new";
let lastPlaylist = null;  // the dialog opens on the playlist you used last

// item: an app spec {app, params}; name: what it is, in words; duration: suggested seconds
export async function addToPlaylist(item, { name = "", duration = 30 } = {}) {
  const j = await api("/api/playlists");
  const names = Object.keys(j.playlists);
  const pick = el("select", { id: "atpList" },
    ...names.map(n => el("option", { value: n, selected: n === lastPlaylist }, n)),
    el("option", { value: NEW, selected: !names.length }, "New playlist…"));
  const newName = el("input", { id: "atpNew", type: "text", maxlength: 60, autocomplete: "off", placeholder: "For example Evening" });
  const newBox = el("label", { class: "dlgField" }, el("span", {}, "Name of the new playlist"), newName);
  const secs = el("input", { id: "atpSecs", type: "number", min: 1, max: 86400, step: 1, value: Math.round(duration) || 30, inputmode: "numeric" });
  const err = el("p", { class: "dlgErr", role: "alert" });
  const add = el("button", { type: "submit", class: "primary" }, "Add");
  const cancel = el("button", { type: "button" }, "Cancel");
  const syncNew = () => { newBox.hidden = pick.value !== NEW; err.textContent = ""; };
  pick.onchange = () => { syncNew(); if (pick.value === NEW) newName.focus(); };
  syncNew();
  const form = el("form", { class: "atpForm" },
    el("h2", { id: "atpTitle" }, "Add to playlist"),
    name ? el("p", { class: "muted dlgMsg" }, name) : null,
    el("label", { class: "dlgField" }, el("span", {}, "Playlist"), pick),
    newBox,
    el("label", { class: "dlgField" }, el("span", {}, "Seconds on the board"), secs),
    err,
    el("div", { class: "row dlgButtons" }, cancel, add));
  const d = el("dialog", { class: "dlg atp", "aria-labelledby": "atpTitle" }, form);
  document.body.append(d);
  cancel.onclick = () => d.close();
  d.addEventListener("close", () => d.remove());
  form.addEventListener("submit", safe(async e => {
    e.preventDefault();
    const creating = pick.value === NEW;
    const playlist = creating ? newName.value.trim() : pick.value;
    if (!playlist) { err.textContent = "Give the new playlist a name."; newName.focus(); return; }
    if (creating && names.includes(playlist)) { err.textContent = `There's already a playlist called "${playlist}".`; newName.focus(); return; }
    const seconds = Math.max(1, Math.round(+secs.value || 0));
    if (!seconds) { err.textContent = "Enter how many seconds it stays on the board."; secs.focus(); return; }
    add.disabled = true;
    let r;
    try {
      r = await api("/api/playlists/add", { playlist, item, duration: seconds, create: creating });
    } catch (ex) {
      add.disabled = false;
      err.textContent = ex.message;
      return;
    }
    lastPlaylist = r.playlist;
    bus.dispatchEvent(new CustomEvent("playlistAdded", { detail: { name: r.playlist } }));
    bus.dispatchEvent(new Event("playlists"));
    const n = r.playlists[r.playlist].items.length;
    const open = el("button", { type: "button", class: "linkLike" }, `Open "${r.playlist}"`);
    const done = el("button", { type: "button", class: "primary" }, "Done");
    open.onclick = () => { d.close(); bus.dispatchEvent(new CustomEvent("openPlaylist", { detail: { name: r.playlist, index: r.index } })); };
    done.onclick = () => d.close();
    form.replaceChildren(
      el("h2", { id: "atpTitle" }, icon("check"), "Added"),
      el("p", { class: "dlgMsg", role: "status" }, `${name || "It"} is item ${r.index + 1} of ${n} in "${r.playlist}", for ${seconds} s.`),
      el("div", { class: "row dlgButtons" }, open, done));
    done.focus();
  }));
  d.showModal();
  (names.length ? pick : newName).focus();
}

// A button for a page's action row. getItem() returns the app spec (or null when there's nothing to add yet).
export function addToPlaylistButton(getItem, { name = () => "", duration = () => 30, label = "Add to playlist", cls = "" } = {}) {
  return el("button", { type: "button", class: cls, title: "Put it in a playlist, or start a new one", onclick: safe(async () => {
    const item = await getItem();
    if (!item) return toast("Nothing to add yet", true);
    await addToPlaylist(item, { name: typeof name === "function" ? name() : name, duration: typeof duration === "function" ? duration() : duration });
  }) }, icon("playlist"), label);
}
