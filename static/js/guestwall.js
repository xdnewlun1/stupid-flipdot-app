// Settings → Guest wall: the moderation queue for messages posted from /guest.
import { $, api, el, safe } from "./common.js";

let timer = null;
const when = ts => new Date(ts * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
const LABEL = { pending: "waiting", approved: "approved", rejected: "rejected" };

function render(info) {
  $("gwLink").href = info.url;
  $("gwLink").textContent = info.url;
  $("gwAuto").checked = info.auto_approve;
  $("gwPending").textContent = info.pending ? `${info.pending} message${info.pending === 1 ? "" : "s"}` : "None";
  $("gwBadge").textContent = info.pending || "";
  $("gwBadge").hidden = !info.pending;
  $("gwBadge").title = `${info.pending} waiting for review`;
  const act = (m, action) => safe(async () => render(await api(`/api/guest/messages/${m.id}/${action}`, {})));
  $("gwList").replaceChildren(...(info.messages.length ? info.messages.map(m => el("div", { class: "item" + (m.status === "pending" ? " editing" : "") },
    el("div", { class: "name" }, m.text, el("small", {}, `${m.name || "Anonymous"} · ${when(m.ts)} · ${LABEL[m.status] || m.status}`)),
    m.status !== "approved" ? el("button", { class: "primary", onclick: act(m, "approve") }, "Approve") : "",
    m.status !== "rejected" ? el("button", { onclick: act(m, "reject") }, m.status === "approved" ? "Hide" : "Reject") : "",
    el("button", { class: "danger", onclick: act(m, "delete") }, "Delete"),
  )) : [el("p", { class: "muted" }, "No messages yet. Share the link above, or run the Guest wall app to show it as a QR code.")]));
}

export async function loadGuestWall() {
  render(await api("/api/guest"));
  clearTimeout(timer);
  // keep the queue fresh while Settings is open
  timer = setTimeout(function tick() {
    if (document.body.dataset.tab !== "settings") return;
    if (!document.hidden) api("/api/guest").then(render).catch(() => {});
    timer = setTimeout(tick, 15000);
  }, 15000);
}

$("gwAuto").onchange = safe(async () => render(await api("/api/guest/settings", { auto_approve: $("gwAuto").checked })));
$("gwRefresh").onclick = safe(loadGuestWall);
