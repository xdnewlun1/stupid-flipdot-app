// Guest wall page: the moderation queue for messages posted from /guest.
import { $, api, el, safe, toast } from "./common.js";

let timer = null;
const when = ts => new Date(ts * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
const LABEL = { pending: "waiting", approved: "approved", rejected: "rejected" };

function render(info) {
  $("gwLink").href = info.url;
  $("gwLink").textContent = info.url;
  $("gwAuto").checked = info.auto_approve;
  $("gwPending").textContent = info.pending ? `${info.pending} message${info.pending === 1 ? "" : "s"}` : "None";
  document.querySelectorAll(".gwCount").forEach(b => {
    b.textContent = info.pending || "";
    b.hidden = !info.pending;
    b.title = `${info.pending} waiting for review`;
  });
  const act = (m, action) => safe(async () => render(await api(`/api/guest/messages/${m.id}/${action}`, {})));
  $("gwList").replaceChildren(...(info.messages.length ? info.messages.map(m => el("div", { class: "item" + (m.status === "pending" ? " editing" : "") },
    el("div", { class: "name" }, m.text, el("small", {}, `${m.name || "Anonymous"} · ${when(m.ts)} · ${LABEL[m.status] || m.status}`)),
    m.status !== "approved" ? el("button", { class: "primary", onclick: act(m, "approve") }, "Approve") : "",
    m.status !== "rejected" ? el("button", { onclick: act(m, "reject") }, m.status === "approved" ? "Hide" : "Reject") : "",
    el("button", { class: "danger", onclick: act(m, "delete") }, "Delete"),
  )) : [el("div", { class: "empty" }, el("p", { class: "muted" }, "No messages yet. Share the link above, or put the Guest wall app on the board: it shows the link as a QR code."),
    el("button", { class: "primary", onclick: safe(async () => { await api("/api/run", { app: "guest_wall" }); toast("Showing the Guest wall with its QR code"); }) }, "Show the QR code on the board"))]));
}

export async function loadGuestWall() {
  render(await api("/api/guest"));
  clearTimeout(timer);
  // keep the queue fresh while the Guest wall page is open
  // (every 15 s on this page, every minute elsewhere, for the count in the navigation)
  let n = 0;
  timer = setTimeout(function tick() {
    const here = document.body.dataset.tab === "guests";
    if (!document.hidden && (here || ++n % 4 === 0)) api("/api/guest").then(render).catch(() => {});
    timer = setTimeout(tick, 15000);
  }, 15000);
}

$("gwAuto").onchange = safe(async () => render(await api("/api/guest/settings", { auto_approve: $("gwAuto").checked })));
$("gwRefresh").onclick = safe(loadGuestWall);
